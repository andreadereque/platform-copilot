"""Context-window management: what goes into each model call, and how much of it.

The model only sees its context window, and every token costs money and latency. Two limits:

- **History budget.** Old turns are dropped first, keeping the most recent ones that fit
  (`history_token_budget`). The system prompt and the new question always go in.
- **Tool results.** A tool can return a lot (rows, chunks). Each result is clipped to
  `tool_result_max_chars` with a visible marker, so the model knows something was cut.

Tokens are estimated as characters / 4, which is close enough for a budget. For exact counts
you would use the provider's tokenizer or its count-tokens endpoint.
"""

from platform_copilot.llm.base import Message

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


def fit_history(history: list[Message], budget_tokens: int) -> list[Message]:
    """The most recent turns whose estimated size fits the budget, in their original order."""
    kept: list[Message] = []
    used = 0
    for message in reversed(history):
        size = estimate_tokens(str(message.get("content", "")))
        if used + size > budget_tokens:
            break
        kept.append(message)
        used += size
    return list(reversed(kept))


def clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = len(text) - max_chars
    return f"{text[:max_chars]}… [truncated {cut} characters]"
