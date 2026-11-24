"""Safety net for model failures: retry the primary with backoff, then use a fallback model.

    primary (e.g. Claude or Gemini) ── fails ──▶ wait 0.5 s, retry ── fails ──▶ fallback (Ollama)

Only `LLMError` triggers it (timeouts, 5xx, refusals mapped to errors). The answer says which
model produced it, so the trace and the audit log show when the fallback was used. A circuit
breaker (stop calling the primary for a minute after N failures) is the next step under load.
"""

import asyncio
from collections.abc import Awaitable, Callable

from platform_copilot.llm.base import ChatReply, LLMError, LLMProvider, Message, ToolSchema


class FallbackProvider:
    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider,
        *,
        retries: int = 1,
        backoff_s: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,  # tests pass a fake
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.retries = retries
        self.backoff_s = backoff_s
        self._sleep = sleep
        self.name = primary.name
        self.model = primary.model

    async def chat(self, messages: list[Message], tools: list[ToolSchema]) -> ChatReply:
        for attempt in range(self.retries + 1):
            try:
                return await self.primary.chat(messages, tools)
            except LLMError:
                if attempt < self.retries:
                    await self._sleep(self.backoff_s * 2**attempt)  # 0.5 s, 1 s, 2 s…
        return await self.fallback.chat(messages, tools)

    async def is_ready(self) -> bool:
        return await self.primary.is_ready() or await self.fallback.is_ready()
