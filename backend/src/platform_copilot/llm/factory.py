"""Pick the provider from the settings, and wrap it with the safety nets."""

from platform_copilot.config import Settings
from platform_copilot.guards import MaskingProvider
from platform_copilot.llm.base import LLMProvider
from platform_copilot.llm.claude import ClaudeProvider
from platform_copilot.llm.fake import FakeProvider
from platform_copilot.llm.fallback import FallbackProvider
from platform_copilot.llm.ollama import OllamaProvider
from platform_copilot.llm.vertex import VertexProvider

EXTERNAL = {"claude", "vertex"}


def build_llm(settings: Settings) -> LLMProvider:
    llm = _base(settings)
    if settings.llm_provider in EXTERNAL and settings.mask_pii_external:
        llm = MaskingProvider(llm)  # personal data does not leave the company as is
    if settings.llm_fallback == "ollama" and settings.llm_provider != "ollama":
        llm = FallbackProvider(llm, OllamaProvider(settings.ollama_url, settings.llm_model))
    return llm


def _base(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "claude":
        api_key = settings.anthropic_api_key or None
        return ClaudeProvider(settings.claude_model, api_key=api_key, effort=settings.claude_effort)
    if settings.llm_provider == "vertex":
        return VertexProvider(settings.gcp_project, settings.gcp_location, settings.vertex_model)
    if settings.llm_provider == "fake":
        return FakeProvider()
    return OllamaProvider(settings.ollama_url, settings.llm_model)
