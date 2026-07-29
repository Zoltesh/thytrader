"""Read-only API tests for durable market-data ingestion diagnostics."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.market_data.models import CandleInterval
from thytrader.market_data.worker_state import (
    InMemoryMarketDataWorkerStateStore,
    MarketDataWorkerAttempt,
    MarketDataWorkerSuccess,
)


def test_ingestion_diagnostics_are_unavailable_without_durable_state() -> None:
    """Absent PostgreSQL configuration must not manufacture worker status."""
    app = create_app(Settings(_env_file=None))

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/ingestion?product_id=BTC-USD")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "market_data_worker_state_unavailable",
            "message": "Market-data ingestion state is unavailable.",
        }
    }


def test_ingestion_diagnostics_report_never_run_without_manufactured_coverage() -> None:
    """An enabled empty repository reports never-run and no freshness or coverage facts."""
    app = create_app(
        Settings(_env_file=None),
        market_data_state_store=InMemoryMarketDataWorkerStateStore(),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/ingestion?product_id=BTC-USD")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "never_run"
    assert body["last_attempt_at"] is None
    assert body["last_success_at"] is None
    assert body["coverage"] is None
    assert body["fresh"] is None
    assert body["failure"] is None


def test_ingestion_diagnostics_expose_verified_success_evidence() -> None:
    """Published coverage exposes exact counts and its immutable fingerprint."""

    async def seed(store: InMemoryMarketDataWorkerStateStore) -> None:
        ends_at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        attempt = MarketDataWorkerAttempt(
            provider="demo",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            attempted_at=ends_at + timedelta(minutes=5),
            requested_starts_at=ends_at - timedelta(days=7),
            requested_ends_at=ends_at,
        )
        await store.record_attempt(attempt)
        await store.record_success(
            MarketDataWorkerSuccess(
                attempt=attempt,
                covered_starts_at=attempt.requested_starts_at,
                covered_ends_at=attempt.requested_ends_at,
                expected_candle_count=168,
                received_candle_count=168,
                gap_count=0,
                missing_intervals=0,
                content_fingerprint="sha256:" + "a" * 64,
            )
        )

    store = InMemoryMarketDataWorkerStateStore()
    asyncio.run(seed(store))
    app = create_app(Settings(_env_file=None), market_data_state_store=store)

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/ingestion?product_id=BTC-USD")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["fresh"] is True
    assert body["enabled"] is True
    assert body["freshness"] == "current"
    assert body["coverage_status"] == "complete"
    assert body["expected_latest_boundary"] is not None
    assert body["next_attempt_at"] is not None
    assert body["dataset_revision"] == 1
    assert body["coverage"] == {
        "starts_at": body["coverage"]["starts_at"],
        "ends_at": body["coverage"]["ends_at"],
        "expected_candle_count": 168,
        "received_candle_count": 168,
        "gap_count": 0,
        "missing_intervals": 0,
        "complete": True,
        "content_fingerprint": "sha256:" + "a" * 64,
    }
    assert body["failure"] is None
