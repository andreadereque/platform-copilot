"""A scripted provider for tests: returns queued replies and records what it was sent."""

from platform_copilot.llm.base import ChatReply, LLMError, Message, ToolSchema


class FakeProvider:
    name = "fake"

    def __init__(self, replies: list[ChatReply | LLMError] | None = None) -> None:
        self.model = "fake-model"
        self.replies: list[ChatReply | LLMError] = list(replies or [])
        self.calls: list[list[Message]] = []
        self.ready = True

    async def chat(self, messages: list[Message], tools: list[ToolSchema]) -> ChatReply:
        self.calls.append(list(messages))
        if not self.replies:
            raise AssertionError("FakeProvider ran out of scripted replies")
        reply = self.replies.pop(0)
        if isinstance(reply, LLMError):
            raise reply
        return reply

    async def is_ready(self) -> bool:
        return self.ready
