"""Live PostgreSQL tests for the CFM futures account mirror store (ADR 0127)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from typing import TYPE_CHECKING

from pydantic import SecretStr
import pytest

from tests.exchanges.test_coinbase_cfm import _transport
from thytrader.exchanges.coinbase_cfm import CoinbaseCfmAccount
from thytrader.exchanges.rest_transport import CoinbaseHttpStatusError
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_futures_account import PostgresFuturesAccountStore
from thytrader.worker.futures_mirror import observe_futures_account

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import Any

    from sqlalchemy.ext.asyncio import AsyncEngine

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


def _run(body: Callable[[AsyncEngine], Awaitable[None]]) -> None:
    """Run one async body against the throwaway test database."""

    async def exercise() -> None:
        """Own the engine lifecycle."""
        if _TEST_DATABASE_URL is None:
            raise AssertionError("PostgreSQL integration URL was not configured.")
        engine = create_engine(SecretStr(_TEST_DATABASE_URL))
        try:
            await body(engine)
        finally:
            await dispose(engine)

    asyncio.run(exercise())


def _unique_instant() -> datetime:
    """A strictly newer instant than anything earlier tests wrote."""
    return datetime.now(UTC) + timedelta(days=3650)


def test_full_observation_round_trips_exactly() -> None:
    """Every USD amount, measure, position and window fact survives a reload."""

    async def body(engine: AsyncEngine) -> None:
        """Record one enabled observation and read it back."""
        store = PostgresFuturesAccountStore(engine)
        observation = await observe_futures_account(
            CoinbaseCfmAccount(_transport()), _unique_instant()
        )
        await store.record(observation)
        assert await store.latest() == observation
        loaded = await store.latest()
        assert loaded is not None
        assert loaded.balance is not None
        assert loaded.balance.cbi_usd_balance == Decimal("425.00")

    _run(body)


def test_unknown_reads_stay_unknown_after_storage() -> None:
    """A failed balance read reloads as unknown enablement with no balance."""

    def fail() -> dict[str, Any]:
        raise CoinbaseHttpStatusError(503, None)

    async def body(engine: AsyncEngine) -> None:
        """Record one degraded observation and read it back."""
        store = PostgresFuturesAccountStore(engine)
        reader = CoinbaseCfmAccount(_transport(balance=fail))
        observation = await observe_futures_account(reader, _unique_instant() + timedelta(1))
        await store.record(observation)
        loaded = await store.latest()
        assert loaded == observation
        assert loaded is not None
        assert loaded.balance is None
        assert loaded.read_failures == ("balance_summary:http_503",)

    _run(body)
