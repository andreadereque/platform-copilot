"""Ollama, running locally: free, and the data never leaves the machine."""

import json
from typing import Any

import httpx

from platform_copilot.llm.base import ChatReply, LLMError, Message, ToolCall, ToolSchema


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout, transport=self._transport
        )

    async def chat(self, messages: list[Message], tools: list[ToolSchema]) -> ChatReply:
        body = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0},
            "messages": [_to_ollama(message) for message in messages],
            "tools": tools,
        }
        try:
            async with self._client() as client:
                response = await client.post("/api/chat", json=body)
                response.raise_for_status()
        except httpx.TimeoutException:
            raise LLMError("Ollama did not answer in time") from None
        except httpx.HTTPError as error:
            raise LLMError(f"Cannot use Ollama: {error}") from None
        return _parse(response.json(), self.model)

    async def is_ready(self) -> bool:
        try:
            async with self._client() as client:
                response = await client.get("/api/tags", timeout=2.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200


def _to_ollama(message: Message) -> dict[str, Any]:
    if message["role"] == "assistant":
        calls = [
            {"function": {"name": call.name, "arguments": call.arguments}}
            for call in message["tool_calls"]
        ]
        return {"role": "assistant", "content": message["content"], "tool_calls": calls}
    if message["role"] == "tool":
        return {"role": "tool", "content": message["content"], "tool_name": message["name"]}
    return {"role": message["role"], "content": message["content"]}


def _parse(payload: dict[str, Any], model: str) -> ChatReply:
    message = payload.get("message") or {}
    calls = []
    for index, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function", {})
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            arguments = _loads_object(arguments)
        calls.append(ToolCall(f"call_{index}", str(function.get("name", "")), arguments))
    return ChatReply(
        text=str(message.get("content", "")),
        tool_calls=calls,
        model=model,
        input_tokens=int(payload.get("prompt_eval_count", 0)),
        output_tokens=int(payload.get("eval_count", 0)),
    )


def _loads_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if isinstance(value, dict):
        return value
    return {}
