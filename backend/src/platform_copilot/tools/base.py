"""What every tool has in common."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from platform_copilot.config import Settings
from platform_copilot.db import Pool
from platform_copilot.embeddings import Embedder
from platform_copilot.users import User


@dataclass(frozen=True)
class ToolContext:
    pool: Pool
    user: User
    embedder: Embedder
    settings: Settings
    conversation_id: str


@dataclass(frozen=True)
class ToolOutput:
    content: dict[str, Any]  # sent back to the model as JSON
    summary: str  # short label for the UI chip, e.g. "4 chunks"
    sources: list[str] = field(default_factory=list)  # citable source ids


class ToolError(Exception):
    """An expected failure. Its message goes back to the model so it can correct itself."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    run: Callable[[Any, ToolContext], Awaitable[ToolOutput]]

    def schema(self) -> dict[str, Any]:
        """The function definition sent to the model (OpenAI/Ollama tool format)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }
