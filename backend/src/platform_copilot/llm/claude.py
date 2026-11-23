"""Claude through the official Anthropic SDK (optional: needs ANTHROPIC_API_KEY).

The assistant's content blocks (thinking included) go back unchanged on every turn, as the
API requires for tool loops. Server-side fallbacks are on: if the model declines, the API
retries on a fallback model inside the same call.
"""

from typing import Any, Literal, cast

import anthropic
from anthropic.types.beta import BetaMessage, BetaMessageParam, BetaToolParam

from platform_copilot.llm.base import ChatReply, LLMError, Message, ToolCall, ToolSchema

FALLBACK_BETA = "server-side-fallback-2026-07-01"
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class ClaudeProvider:
    name = "claude"

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        effort: Effort = "medium",
        http_client: anthropic.DefaultAsyncHttpxClient | None = None,
    ) -> None:
        self.model = model
        self._effort = effort
        self._client = anthropic.AsyncAnthropic(api_key=api_key, http_client=http_client)

    async def chat(self, messages: list[Message], tools: list[ToolSchema]) -> ChatReply:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        try:
            response = await self._client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=cast(list[BetaMessageParam], _to_claude(messages)),
                tools=[_tool(schema) for schema in tools],
                output_config={"effort": self._effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIConnectionError:
            raise LLMError("Cannot reach the Claude API") from None
        except anthropic.APIStatusError as error:
            raise LLMError(f"Claude API error {error.status_code}: {error.message}") from None
        if response.stop_reason == "refusal":
            raise LLMError("The model declined to answer this request")
        return _parse(response, self.model)

    async def is_ready(self) -> bool:
        return True  # no cheap health endpoint; failures surface per request


def _tool(schema: ToolSchema) -> BetaToolParam:
    function = schema["function"]
    return {
        "name": function["name"],
        "description": function["description"],
        "input_schema": function["parameters"],
    }


def _to_claude(messages: list[Message]) -> list[dict[str, Any]]:
    """System messages go to `system`; consecutive tool results share one user message."""
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "user":
            converted.append({"role": "user", "content": message["content"]})
        if role == "assistant":
            converted.append({"role": "assistant", "content": message["raw"]})
        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message["tool_call_id"],
                "content": message["content"],
                "is_error": message["is_error"],
            }
            previous = converted[-1]
            if previous["role"] == "user" and isinstance(previous["content"], list):
                previous["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
    return converted


def _parse(response: BetaMessage, model: str) -> ChatReply:
    texts = []
    calls = []
    for block in response.content:
        if block.type == "text":
            texts.append(block.text)
        if block.type == "tool_use":
            calls.append(ToolCall(block.id, block.name, dict(block.input)))
    return ChatReply(
        text="".join(texts),
        tool_calls=calls,
        model=response.model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        raw=response.content,
    )
