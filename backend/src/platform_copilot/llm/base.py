"""The one interface the agent uses, whatever the provider.

Messages use a neutral format:
  {"role": "system" | "user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [ToolCall], "raw": provider data}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str, "is_error": bool}
Each provider translates it to its own API.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

Message = dict[str, Any]
ToolSchema = dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0  # filled in by observability.TracedProvider from the price settings
    raw: Any = None  # provider content to send back unchanged on the next turn


class LLMError(Exception):
    """The model could not answer (unreachable, timeout, refusal, bad response)."""


class LLMProvider(Protocol):
    name: str
    model: str

    async def chat(self, messages: list[Message], tools: list[ToolSchema]) -> ChatReply: ...

    async def is_ready(self) -> bool: ...


def assistant_message(reply: ChatReply) -> Message:
    return {
        "role": "assistant",
        "content": reply.text,
        "tool_calls": reply.tool_calls,
        "raw": reply.raw,
    }
