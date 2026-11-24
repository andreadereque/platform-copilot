"""App factory: `uvicorn platform_copilot.main:create_app --factory`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from platform_copilot.api import access_requests, chat, health
from platform_copilot.auth import JwksCache, OidcConfig, TokenValidator
from platform_copilot.config import Settings, get_settings
from platform_copilot.db import make_pool
from platform_copilot.embeddings import Embedder, build_embedder
from platform_copilot.llm.base import LLMProvider
from platform_copilot.llm.factory import build_llm
from platform_copilot.observability import Prices, TracedProvider, setup_tracing
from platform_copilot.ratelimit import RateLimiter


def create_app(
    settings: Settings | None = None,
    *,
    llm: LLMProvider | None = None,
    embedder: Embedder | None = None,
    token_validator: TokenValidator | None = None,
) -> FastAPI:
    """Build the app. Tests inject fakes (`llm`, `embedder`, a validator whose keys come from a
    mock transport); otherwise settings decide."""
    if settings is None:
        settings = get_settings()
    if llm is None:
        llm = build_llm(settings)
    if settings.otel_exporter == "console":
        setup_tracing()
    prices = Prices(settings.llm_price_input_per_mtok, settings.llm_price_output_per_mtok)
    llm = TracedProvider(llm, prices)
    if embedder is None:
        embedder = build_embedder(settings)
    if token_validator is None:
        config = OidcConfig.from_settings(settings)
        token_validator = TokenValidator(config, JwksCache(config.jwks_url))
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
    app.state.token_validator = token_validator
    app.state.chat_limiter = RateLimiter(settings.chat_burst, settings.chat_per_minute / 60)
    app.include_router(health.router)
    app.include_router(access_requests.router)
    app.include_router(chat.router)
    return app
