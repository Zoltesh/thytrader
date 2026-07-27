"""FastAPI application construction."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from thytrader import __version__
from thytrader.api.routes.health import router as health_router
from thytrader.config import Settings
from thytrader.runtime import RuntimeState

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create a configured ThyTrader API application."""
    resolved_settings = settings or Settings()
    runtime = RuntimeState(settings=resolved_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Mark the process ready only while application lifespan is active."""
        runtime.ready = True
        try:
            yield
        finally:
            runtime.ready = False

    app = FastAPI(title="ThyTrader API", version=__version__, lifespan=lifespan)
    app.state.runtime = runtime
    app.include_router(health_router)
    return app
