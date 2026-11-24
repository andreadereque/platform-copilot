"""Runtime configuration, read from environment variables (and `.env` in development)."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://localhost/platform_copilot"
    llm_provider: Literal["ollama", "claude", "vertex", "fake"] = "ollama"
    llm_model: str = "qwen2.5:3b"
    embed_provider: Literal["ollama", "fake"] = "ollama"
    embed_model: str = "nomic-embed-text"
    ollama_url: str = "http://localhost:11434"
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_steps: int = 5
    search_fallback: bool = True  # if the first reply uses no tool, search the docs and retry
    tool_timeout_s: float = 10.0
    catalog_timeout_ms: int = 2000
    catalog_row_limit: int = 50
    log_level: str = "INFO"
    # GCP: Gemini on Vertex AI, with Application Default Credentials.
    gcp_project: str = ""
    gcp_location: str = "europe-west1"
    vertex_model: str = "gemini-2.5-flash"
    # Safety nets: a local fallback model, and PII masked before it reaches an external model.
    llm_fallback: Literal["none", "ollama"] = "none"
    mask_pii_external: bool = True
    # OIDC: the API accepts access tokens issued by this realm for this audience.
    oidc_issuer: str = "http://localhost:8080/realms/platform-copilot"
    oidc_audience: str = "platform-copilot-api"
    oidc_jwks_url: str = ""  # empty: Keycloak's default, <issuer>/protocol/openid-connect/certs
    # /api/chat rate limit per user: a burst of `chat_burst` questions, then `chat_per_minute`.
    chat_burst: int = 5
    chat_per_minute: float = 10
    # Context engineering: which prompt version runs, and how much context goes in.
    system_prompt_version: str = "v1"
    history_token_budget: int = 2000
    tool_result_max_chars: int = 6000
    # Observability: "console" prints the spans; prices in USD per million tokens (0 = local).
    otel_exporter: Literal["none", "console"] = "none"
    llm_price_input_per_mtok: float = 0.0
    llm_price_output_per_mtok: float = 0.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
