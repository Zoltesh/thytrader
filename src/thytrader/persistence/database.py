"""Async PostgreSQL infrastructure managed by the FastAPI lifespan."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    create_async_engine,
)

if TYPE_CHECKING:
    from pydantic import SecretStr


def create_engine(database_url: SecretStr) -> AsyncEngine:
    """Create an async engine with connection-pool pre-ping enabled.

    The secret value is extracted only at this engine-creation boundary and is
    never logged or returned.
    """
    return create_async_engine(
        database_url.get_secret_value(),
        pool_pre_ping=True,
        echo=False,
    )


async def ping(engine: AsyncEngine) -> None:
    """Execute a lightweight ``SELECT 1`` to verify database connectivity.

    Raises any underlying database exception so callers can fail startup.
    """
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def dispose(engine: AsyncEngine) -> None:
    """Dispose of all connection-pool resources on shutdown."""
    await engine.dispose()
