"""App factory: `uvicorn platform_copilot.main:create_app --factory`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from platform_copilot.api import access_requests, chat, health
from platform_copilot.config import Settings, get_settings
from platform_copilot.db import make_pool
from platform_copilot.embeddings import Embedder, build_embedder
from platform_copilot.llm.base import LLMProvider
from platform_copilot.llm.factory import build_llm


def create_app(
    settings: Settings | None = None,
    *,
    llm: LLMProvider | None = None,
    embedder: Embedder | None = None,
) -> FastAPI:
    """Build the app. Tests inject fake `llm` and `embedder`; otherwise settings decide."""
    if settings is None:
        settings = get_settings()
    if llm is None:
        llm = build_llm(settings)
    if embedder is None:
        embedder = build_embedder(settings)
    pool = make_pool(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await pool.open()
        yield
        await pool.close()

    app = FastAPI(title="platform-copilot", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.pool = pool
    app.state.llm = llm
    app.state.embedder = embedder
    app.include_router(health.router)
    app.include_router(access_requests.router)
    app.include_router(chat.router)
    return app
