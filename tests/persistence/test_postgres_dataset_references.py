"""PostgreSQL integration coverage for the dataset-retention reference scan."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os
from uuid import uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy import delete

from thytrader.market_data.models import CandleInterval
from thytrader.market_data.worker_state import MarketDataWorkerAttempt, MarketDataWorkerSuccess
from thytrader.persistence.audit_events import AuditEvent, AuditEventCategory, AuditEventOutcome
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_audit_events import PostgresAuditEventStore
from thytrader.persistence.postgres_dataset_references import PostgresDatasetReferenceSource
from thytrader.persistence.postgres_market_data_worker import PostgresMarketDataWorkerStateStore
from thytrader.persistence.schema import audit_events, market_data_worker_state

_TEST_DATABASE_URL = os.environ.get("THYTRADER_TEST_DATABASE_URL") or None
pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


def test_reference_scan_finds_typed_and_free_text_fingerprints() -> None:
    """Fingerprints in typed columns and inside free text both count as references."""

    async def exercise() -> None:
        if _TEST_DATABASE_URL is None:
            raise AssertionError("PostgreSQL integration URL was not configured.")
        engine = create_engine(SecretStr(_TEST_DATABASE_URL))
        provider = f"refs-{uuid4().hex[:20]}"
        state_fingerprint = "sha256:" + uuid4().hex + uuid4().hex
        text_fingerprint = "sha256:" + uuid4().hex + uuid4().hex
        attempt = MarketDataWorkerAttempt(
            provider=provider,
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            attempted_at=datetime(2026, 9, 1, 3, 5, tzinfo=UTC),
            requested_starts_at=datetime(2026, 9, 1, tzinfo=UTC),
            requested_ends_at=datetime(2026, 9, 1, 3, tzinfo=UTC),
        )
        try:
            store = PostgresMarketDataWorkerStateStore(engine)
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
                    content_fingerprint=state_fingerprint,
                )
            )
            await PostgresAuditEventStore(engine).append(
                AuditEvent(
                    occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                    category=AuditEventCategory.MARKET_DATA,
                    action="reference_scan_test",
                    outcome=AuditEventOutcome.INFO,
                    detail=f"bound dataset {text_fingerprint} in prose",
                    provider=provider,
                )
            )
            found = await PostgresDatasetReferenceSource(engine).referenced_fingerprints()
            assert state_fingerprint in found
            assert text_fingerprint in found
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(market_data_worker_state).where(
                        market_data_worker_state.c.provider == provider
                    )
                )
                await connection.execute(
                    delete(audit_events).where(audit_events.c.provider == provider)
                )
            await dispose(engine)

    asyncio.run(exercise())
