"""Runtime configuration, read from environment variables (and `.env` in development)."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://localhost/platform_copilot"
    llm_provider: Literal["ollama", "claude", "fake"] = "ollama"
    llm_model: str = "qwen2.5:3b"
    embed_provider: Literal["ollama", "fake"] = "ollama"
    embed_model: str = "nomic-embed-text"
    ollama_url: str = "http://localhost:11434"
    anthropic_api_key: str = ""
    max_steps: int = 5
    tool_timeout_s: float = 10.0
    catalog_timeout_ms: int = 2000
    catalog_row_limit: int = 50
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
