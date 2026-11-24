"""Input guards: flag likely prompt injection, and mask personal data before it leaves.

These are a first line, not the defence. The defences that matter do not depend on the model
(permissions in the SQL, a read-only role, human approval of writes). Guards reduce noise and
exposure:

- **Prompt injection** in the user's question or in retrieved documents is *flagged* (audit
  log, trace) instead of blocked: a regex blocks legitimate questions too ("how do I ignore
  previous results?"). Flags feed monitoring; a classifier model is the step after.
- **PII masking.** Emails, phone numbers, IBANs and Spanish DNI/NIE are replaced with tokens
  before the text goes to an *external* model (Claude, Gemini). A local model sees the original.
"""

import re

from platform_copilot.llm.base import ChatReply, LLMProvider, Message, ToolSchema

INJECTION = re.compile(
    r"ignore (all |any )?(previous|prior|above) instructions"
    r"|disregard (the|your) (rules|instructions)"
    r"|you are now\b"
    r"|reveal (the|your) system prompt"
    r"|ignora (todas )?(las |tus )?instrucciones",
    re.IGNORECASE,
)

PII = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}\b")),
    ("DNI", re.compile(r"\b[XYZ]?\d{7,8}[A-Z]\b")),
    ("PHONE", re.compile(r"(?<!\w)\+?\d[\d ]{7,14}\d(?!\w)")),
]


def injection_suspected(text: str) -> bool:
    return INJECTION.search(text) is not None


def mask_pii(text: str) -> str:
    for label, pattern in PII:
        text = pattern.sub(f"[{label}]", text)
    return text


class MaskingProvider:
    """Wraps an external provider: user and tool messages are masked before they are sent."""

    def __init__(self, inner: LLMProvider) -> None:
        self.inner = inner
        self.name = inner.name
        self.model = inner.model

    async def chat(self, messages: list[Message], tools: list[ToolSchema]) -> ChatReply:
        return await self.inner.chat([_masked(message) for message in messages], tools)

    async def is_ready(self) -> bool:
        return await self.inner.is_ready()


def _masked(message: Message) -> Message:
    if message["role"] in ("user", "tool"):
        return {**message, "content": mask_pii(message["content"])}
    return message
