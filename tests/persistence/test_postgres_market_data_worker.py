"""PostgreSQL integration coverage for durable market-data worker state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy import delete

from thytrader.market_data.models import CandleInterval
from thytrader.market_data.worker_state import (
    MarketDataWorkerAttempt,
    MarketDataWorkerFailure,
    MarketDataWorkerStatus,
    MarketDataWorkerSuccess,
)
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_market_data_worker import PostgresMarketDataWorkerStateStore
from thytrader.persistence.schema import market_data_worker_state

_TEST_DATABASE_URL = os.environ.get("THYTRADER_TEST_DATABASE_URL") or None
pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


def test_postgres_worker_state_survives_restart_and_preserves_verified_coverage() -> None:
    """Real PostgreSQL transitions retain coverage across failure and repository restart."""

    async def exercise() -> None:
        if _TEST_DATABASE_URL is None:
            raise AssertionError("PostgreSQL integration URL was not configured.")
        writer_engine = create_engine(SecretStr(_TEST_DATABASE_URL))
        provider = f"integration-{uuid4().hex[:20]}"
        product_id = "BTC-USD"
        timeframe = CandleInterval.ONE_HOUR
        attempt = MarketDataWorkerAttempt(
            provider=provider,
            product_id=product_id,
            timeframe=timeframe,
            attempted_at=datetime(2026, 7, 29, 3, 5, tzinfo=UTC),
            requested_starts_at=datetime(2026, 7, 29, tzinfo=UTC),
            requested_ends_at=datetime(2026, 7, 29, 3, tzinfo=UTC),
        )
        store = PostgresMarketDataWorkerStateStore(writer_engine)
        write_complete = False
        try:
            await store.record_attempt(attempt)
            await store.record_success(
                MarketDataWorkerSuccess(
                    attempt=attempt,
                    covered_starts_at=attempt.requested_starts_at,
                    covered_ends_at=attempt.requested_ends_at,
                    expected_candle_count=3,
                    received_candle_count=3,
                    gap_count=0,
                    missing_intervals=0,
                    content_fingerprint="sha256:" + "a" * 64,
                )
            )
            failed_attempt = MarketDataWorkerAttempt(
                provider=provider,
                product_id=product_id,
                timeframe=timeframe,
                attempted_at=attempt.attempted_at + timedelta(minutes=5),
                requested_starts_at=attempt.requested_starts_at,
                requested_ends_at=attempt.requested_ends_at,
            )
            await store.record_attempt(failed_attempt)
            await store.record_failure(
                MarketDataWorkerFailure(
                    attempt=failed_attempt,
                    code="provider_unavailable",
                    message="Historical market-data retrieval failed.",
                )
            )
            second_failed_attempt = MarketDataWorkerAttempt(
                provider=provider,
                product_id=product_id,
                timeframe=timeframe,
                attempted_at=failed_attempt.attempted_at + timedelta(minutes=5),
                requested_starts_at=attempt.requested_starts_at,
                requested_ends_at=attempt.requested_ends_at,
                expected_consecutive_failures=1,
            )
            await store.record_attempt(second_failed_attempt)
            await store.record_failure(
                MarketDataWorkerFailure(
                    attempt=second_failed_attempt,
                    code="provider_unavailable",
                    message="Historical market-data retrieval failed.",
                )
            )
            write_complete = True
        finally:
            try:
                if not write_complete:
                    async with writer_engine.begin() as connection:
                        await connection.execute(
                            delete(market_data_worker_state).where(
                                market_data_worker_state.c.provider == provider,
                                market_data_worker_state.c.product_id == product_id,
                                market_data_worker_state.c.timeframe == timeframe.value,
                            )
                        )
            finally:
                await dispose(writer_engine)

        reader_engine = create_engine(SecretStr(_TEST_DATABASE_URL))
        restarted_store = PostgresMarketDataWorkerStateStore(reader_engine)
        try:
            state = await restarted_store.get(provider, product_id, timeframe)

            assert state is not None
            assert state.status is MarketDataWorkerStatus.FAILED
            assert state.last_attempt_at == second_failed_attempt.attempted_at
            assert state.last_success_at == attempt.attempted_at
            assert state.covered_starts_at == attempt.requested_starts_at
            assert state.covered_ends_at == attempt.requested_ends_at
            assert state.expected_candle_count == 3
            assert state.received_candle_count == 3
            assert state.complete is True
            assert state.content_fingerprint == "sha256:" + "a" * 64
            assert state.failure_code == "provider_unavailable"
            assert state.consecutive_failures == 2
        finally:
            try:
                async with reader_engine.begin() as connection:
                    await connection.execute(
                        delete(market_data_worker_state).where(
                            market_data_worker_state.c.provider == provider,
                            market_data_worker_state.c.product_id == product_id,
                            market_data_worker_state.c.timeframe == timeframe.value,
                        )
                    )
            finally:
                await dispose(reader_engine)

    asyncio.run(exercise())


@pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)
def test_postgres_rejects_attempt_planned_from_stale_failure_snapshot() -> None:
    """Two workers cannot commit a failure using the same durable retry-policy snapshot."""

    async def exercise() -> None:
        if _TEST_DATABASE_URL is None:
            raise AssertionError("PostgreSQL integration URL was not configured.")
        first_engine = create_engine(SecretStr(_TEST_DATABASE_URL))
        second_engine = create_engine(SecretStr(_TEST_DATABASE_URL))
        first_store = PostgresMarketDataWorkerStateStore(first_engine)
        second_store = PostgresMarketDataWorkerStateStore(second_engine)
        provider = f"race-{uuid4().hex[:20]}"
        product_id = "BTC-USD"
        timeframe = CandleInterval.ONE_HOUR
        base_at = datetime(2026, 7, 29, 4, 5, tzinfo=UTC)
        base_attempt = MarketDataWorkerAttempt(
            provider=provider,
            product_id=product_id,
            timeframe=timeframe,
            attempted_at=base_at,
            requested_starts_at=base_at - timedelta(hours=3, minutes=5),
            requested_ends_at=base_at - timedelta(minutes=5),
        )

        try:
            assert await first_store.record_attempt(base_attempt) is True
            await first_store.record_success(
                MarketDataWorkerSuccess(
                    attempt=base_attempt,
                    covered_starts_at=base_attempt.requested_starts_at,
                    covered_ends_at=base_attempt.requested_ends_at,
                    expected_candle_count=3,
                    received_candle_count=3,
                    gap_count=0,
                    missing_intervals=0,
                    content_fingerprint="sha256:" + "b" * 64,
                )
            )
            stale_snapshot = await second_store.get(provider, product_id, timeframe)
            assert stale_snapshot is not None
            assert stale_snapshot.consecutive_failures == 0

            accepted_attempt = MarketDataWorkerAttempt(
                provider=provider,
                product_id=product_id,
                timeframe=timeframe,
                attempted_at=base_at + timedelta(minutes=5),
                requested_starts_at=base_attempt.requested_starts_at,
                requested_ends_at=base_attempt.requested_ends_at,
                expected_consecutive_failures=stale_snapshot.consecutive_failures,
            )
            assert await first_store.record_attempt(accepted_attempt) is True
            accepted_retry_at = accepted_attempt.attempted_at + timedelta(seconds=300)
            await first_store.record_failure(
                MarketDataWorkerFailure(
                    attempt=accepted_attempt,
                    code="provider_unavailable",
                    message="Historical market-data retrieval failed.",
                    next_retry_at=accepted_retry_at,
                )
            )

            stale_attempt = MarketDataWorkerAttempt(
                provider=provider,
                product_id=product_id,
                timeframe=timeframe,
                attempted_at=accepted_attempt.attempted_at + timedelta(seconds=1),
                requested_starts_at=base_attempt.requested_starts_at,
                requested_ends_at=base_attempt.requested_ends_at,
                expected_consecutive_failures=stale_snapshot.consecutive_failures,
            )
            assert await second_store.record_attempt(stale_attempt) is False

            final = await second_store.get(provider, product_id, timeframe)
            assert final is not None
            assert final.status is MarketDataWorkerStatus.FAILED
            assert final.last_attempt_at == accepted_attempt.attempted_at
            assert final.consecutive_failures == 1
            assert final.next_retry_at == accepted_retry_at
        finally:
            async with first_engine.begin() as connection:
                await connection.execute(
                    delete(market_data_worker_state).where(
                        market_data_worker_state.c.provider == provider,
                        market_data_worker_state.c.product_id == product_id,
                        market_data_worker_state.c.timeframe == timeframe.value,
                    )
                )
            await dispose(second_engine)
            await dispose(first_engine)

    asyncio.run(exercise())
