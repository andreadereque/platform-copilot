"""Gemini on Vertex AI (GCP), through the REST API with function calling.

Authentication is Application Default Credentials: on Cloud Run, the service account of the
service; on a laptop, `gcloud auth application-default login`. No API key in the code.

Message mapping (our neutral format → Gemini):
- system           → systemInstruction
- user             → role "user", a text part
- assistant        → role "model", text and functionCall parts
- tool result      → role "user", a functionResponse part
Gemini's tool calls have no stable id, so we number them.
"""

import json
from collections.abc import Callable
from typing import Any

import google.auth
import google.auth.exceptions
import google.auth.transport.requests
import httpx

from platform_copilot.llm.base import ChatReply, LLMError, Message, ToolCall, ToolSchema

SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def adc_token() -> str:
    """An OAuth access token from Application Default Credentials."""
    credentials, _project = google.auth.default(scopes=[SCOPE])
    credentials.refresh(google.auth.transport.requests.Request())  # type: ignore[no-untyped-call]
    return str(credentials.token)


class VertexProvider:
    name = "vertex"

    def __init__(
        self,
        project: str,
        location: str,
        model: str,
        *,
        token: Callable[[], str] = adc_token,  # tests pass a fake
        timeout_s: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._url = (
            f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
            f"/locations/{location}/publishers/google/models/{model}:generateContent"
        )
        self._token = token
        self._timeout = timeout_s
        self._transport = transport

    async def chat(self, messages: list[Message], tools: list[ToolSchema]) -> ChatReply:
        try:
            headers = {"Authorization": f"Bearer {self._token()}"}
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
                response = await c.post(
                    self._url, json=to_request(messages, tools), headers=headers
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            raise LLMError("Vertex AI did not answer in time") from None
        except (httpx.HTTPError, google.auth.exceptions.GoogleAuthError) as error:
            raise LLMError(f"Cannot use Vertex AI: {error}") from None
        return parse(response.json(), self.model)

    async def is_ready(self) -> bool:
        return True  # no cheap health endpoint; failures surface as LLMError per call


def to_request(messages: list[Message], tools: list[ToolSchema]) -> dict[str, Any]:
    system = [m["content"] for m in messages if m["role"] == "system"]
    body: dict[str, Any] = {
        "contents": [_content(m) for m in messages if m["role"] != "system"],
        "generationConfig": {"temperature": 0},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
    if tools:
        declarations = [
            {
                "name": tool["function"]["name"],
                "description": tool["function"]["description"],
                "parametersJsonSchema": tool["function"]["parameters"],
            }
            for tool in tools
        ]
        body["tools"] = [{"functionDeclarations": declarations}]
    return body


def _content(message: Message) -> dict[str, Any]:
    if message["role"] == "assistant":
        parts: list[dict[str, Any]] = []
        if message["content"]:
            parts.append({"text": message["content"]})
        for call in message.get("tool_calls") or []:
            parts.append({"functionCall": {"name": call.name, "args": call.arguments}})
        return {"role": "model", "parts": parts}
    if message["role"] == "tool":
        result = {"content": message["content"], "is_error": message["is_error"]}
        part = {"functionResponse": {"name": message["name"], "response": result}}
        return {"role": "user", "parts": [part]}
    return {"role": "user", "parts": [{"text": message["content"]}]}


def parse(data: dict[str, Any], model: str) -> ChatReply:
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        reason = json.dumps(data.get("promptFeedback") or data)[:200]
        raise LLMError(f"Vertex AI returned no answer: {reason}") from None
    text = "".join(part.get("text", "") for part in parts)
    calls = [
        ToolCall(f"call-{index}", part["functionCall"]["name"], part["functionCall"]["args"])
        for index, part in enumerate(parts)
        if "functionCall" in part
    ]
    usage = data.get("usageMetadata", {})
    return ChatReply(
        text,
        calls,
        model=model,
        input_tokens=usage.get("promptTokenCount", 0),
        output_tokens=usage.get("candidatesTokenCount", 0),
    )
