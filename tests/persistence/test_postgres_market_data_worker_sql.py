"""Database-free SQL compilation coverage for market-data worker compare-and-set guards."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import SecretStr
from sqlalchemy.dialects import postgresql

from thytrader.market_data.models import CandleInterval
from thytrader.market_data.worker_state import (
    MarketDataWorkerAttempt,
    MarketDataWorkerFailure,
    MarketDataWorkerSuccess,
)
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_market_data_worker import PostgresMarketDataWorkerStateStore

if TYPE_CHECKING:
    import pytest
    from sqlalchemy.sql.elements import ClauseElement


def test_postgres_worker_transitions_compile_idempotent_compare_and_set_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PostgreSQL upserts reject stale attempts and equal-timestamp terminal replays."""

    async def exercise() -> None:
        engine = create_engine(SecretStr("postgresql+asyncpg://unused:unused@127.0.0.1:1/unused"))
        store = PostgresMarketDataWorkerStateStore(engine)
        statements: list[str] = []

        async def capture(statement: ClauseElement) -> bool:
            statements.append(str(statement.compile(dialect=postgresql.dialect())))
            return True

        monkeypatch.setattr(store, "_execute", capture)
        attempted_at = datetime(2026, 7, 29, 4, 5, tzinfo=UTC)
        attempt = MarketDataWorkerAttempt(
            provider="coinbase",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            attempted_at=attempted_at,
            requested_starts_at=attempted_at - timedelta(hours=4, minutes=5),
            requested_ends_at=attempted_at - timedelta(minutes=5),
        )
        try:
            await store.record_attempt(attempt)
            await store.record_success(
                MarketDataWorkerSuccess(
                    attempt=attempt,
                    covered_starts_at=attempt.requested_starts_at,
                    covered_ends_at=attempt.requested_ends_at,
                    expected_candle_count=4,
                    received_candle_count=4,
                    gap_count=0,
                    missing_intervals=0,
                    content_fingerprint="sha256:" + "a" * 64,
                )
            )
            await store.record_failure(
                MarketDataWorkerFailure(
                    attempt=attempt,
                    code="provider_unavailable",
                    message="Historical market-data retrieval failed.",
                )
            )
        finally:
            await dispose(engine)

        assert len(statements) == 3
        attempt_sql, success_sql, failure_sql = statements
        attempt_guard = attempt_sql.split("DO UPDATE SET", maxsplit=1)[1].split(
            "WHERE", maxsplit=1
        )[1]
        assert "last_attempt_at <" in attempt_sql
        assert "consecutive_failures =" in attempt_guard
        assert "status =" not in attempt_guard
        assert "last_attempt_at <" in success_sql
        assert "last_attempt_at =" in success_sql
        assert "status =" in success_sql
        assert "covered_ends_at <=" in success_sql
        assert "consecutive_failures =" in success_sql
        assert "last_attempt_at <" in failure_sql
        assert "last_attempt_at =" in failure_sql
        assert "status =" in failure_sql
        assert "consecutive_failures =" in failure_sql

    asyncio.run(exercise())
