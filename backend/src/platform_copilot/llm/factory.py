"""Pick the provider from the settings."""

from platform_copilot.config import Settings
from platform_copilot.llm.base import LLMProvider
from platform_copilot.llm.claude import ClaudeProvider
from platform_copilot.llm.fake import FakeProvider
from platform_copilot.llm.ollama import OllamaProvider


def build_llm(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "claude":
        api_key = settings.anthropic_api_key or None
        return ClaudeProvider(settings.claude_model, api_key=api_key, effort=settings.claude_effort)
    if settings.llm_provider == "fake":
        return FakeProvider()
    return OllamaProvider(settings.ollama_url, settings.llm_model)
