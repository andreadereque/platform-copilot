"""App factory: `uvicorn platform_copilot.main:create_app --factory`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from platform_copilot.api import health
from platform_copilot.config import Settings, get_settings
from platform_copilot.db import make_pool


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()
    pool = make_pool(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await pool.open()
        yield
        await pool.close()

    app = FastAPI(title="platform-copilot", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.pool = pool
    app.include_router(health.router)
    return app
