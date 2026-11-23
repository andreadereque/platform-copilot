"""Turn text into vectors. Ollama in real use; a deterministic fake in tests (no network)."""

import hashlib
import math
import re
from typing import Protocol

import httpx

from platform_copilot.config import Settings

DIMENSIONS = 768


class EmbeddingError(Exception):
    pass


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_s: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_s
        self._transport = transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        body = {"model": self._model, "input": texts}
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post("/api/embed", json=body)
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise EmbeddingError(f"Cannot embed with Ollama: {error}") from None
        vectors: list[list[float]] = response.json()["embeddings"]
        return vectors


class FakeEmbedder:
    """Bag of words hashed into 768 buckets: texts that share words get similar vectors."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_hash_vector(text) for text in texts]


def _hash_vector(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for word in re.findall(r"\w+", text.lower()):
        bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % DIMENSIONS
        vector[bucket] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def to_pgvector(vector: list[float]) -> str:
    """pgvector accepts '[0.1,0.2,...]' cast to ::vector."""
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


def build_embedder(settings: Settings) -> Embedder:
    if settings.embed_provider == "fake":
        return FakeEmbedder()
    return OllamaEmbedder(settings.ollama_url, settings.embed_model)
