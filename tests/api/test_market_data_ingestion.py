"""Read-only API tests for durable market-data ingestion diagnostics."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
import pytest

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.market_data.feed_state import (
    DisabledMarketFeedStateStore,
    InMemoryMarketFeedStateStore,
    MarketFeedSnapshot,
    MarketFeedState,
)
from thytrader.market_data.models import CandleInterval
from thytrader.market_data.worker_state import (
    DisabledMarketDataWorkerStateStore,
    InMemoryMarketDataWorkerStateStore,
    MarketDataWorkerAttempt,
    MarketDataWorkerFailure,
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


def test_ingestion_diagnostics_mark_unverified_dataset_unavailable() -> None:
    """Retained historical facts do not imply availability after verification failure."""

    async def seed(store: InMemoryMarketDataWorkerStateStore) -> None:
        ends_at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        attempt = MarketDataWorkerAttempt(
            provider="demo",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            attempted_at=ends_at + timedelta(minutes=5),
            requested_starts_at=ends_at - timedelta(hours=3),
            requested_ends_at=ends_at,
        )
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
        failed_attempt = replace(
            attempt,
            attempted_at=attempt.attempted_at + timedelta(minutes=5),
        )
        await store.record_attempt(failed_attempt)
        await store.record_failure(
            MarketDataWorkerFailure(
                attempt=failed_attempt,
                code="dataset_verification_failed",
                message="The current market-data dataset could not be verified.",
                next_retry_at=failed_attempt.attempted_at + timedelta(minutes=5),
            )
        )

    store = InMemoryMarketDataWorkerStateStore()
    asyncio.run(seed(store))
    app = create_app(Settings(_env_file=None), market_data_state_store=store)

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/ingestion?product_id=BTC-USD")

    assert response.status_code == 200
    body = response.json()
    assert body["coverage"] is not None
    assert body["coverage"]["complete"] is True
    assert body["coverage_status"] == "unavailable"
    assert body["failure"]["code"] == "dataset_verification_failed"


def test_ingestion_diagnostics_reject_coverage_without_success_instant() -> None:
    """Forged verified coverage without its establishing success must fail closed at HTTP."""

    async def seed(store: InMemoryMarketDataWorkerStateStore) -> None:
        ends_at = datetime(2026, 8, 1, 3, tzinfo=UTC)
        attempt = MarketDataWorkerAttempt(
            provider="demo",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            attempted_at=ends_at,
            requested_starts_at=ends_at - timedelta(hours=3),
            requested_ends_at=ends_at,
        )
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

    store = InMemoryMarketDataWorkerStateStore()
    asyncio.run(seed(store))
    state = asyncio.run(store.get("demo", "BTC-USD", CandleInterval.ONE_HOUR))
    assert state is not None
    object.__setattr__(state, "last_success_at", None)
    app = create_app(Settings(_env_file=None), market_data_state_store=store)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/market-data/ingestion?product_id=BTC-USD")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "market_data_worker_state_unavailable",
            "message": "Market-data ingestion state is unavailable.",
        }
    }


def test_ingestion_diagnostics_fail_closed_for_unrepresentable_retry_time() -> None:
    """Persisted max-date retries must not leak an arithmetic exception through the API."""

    async def seed(store: InMemoryMarketDataWorkerStateStore) -> None:
        attempted_at = datetime.max.replace(tzinfo=UTC)
        await store.record_attempt(
            MarketDataWorkerAttempt(
                provider="demo",
                product_id="BTC-USD",
                timeframe=CandleInterval.ONE_HOUR,
                attempted_at=attempted_at,
                requested_starts_at=attempted_at,
                requested_ends_at=attempted_at,
            )
        )

    store = InMemoryMarketDataWorkerStateStore()
    asyncio.run(seed(store))
    app = create_app(Settings(_env_file=None), market_data_state_store=store)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/market-data/ingestion?product_id=BTC-USD")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "market_data_worker_state_unavailable",
            "message": "Market-data ingestion state is unavailable.",
        }
    }


def test_ingestion_diagnostics_reject_forged_naive_retry_timestamp() -> None:
    """A forged persisted retry timestamp must not serialize as a successful response."""

    async def seed(store: InMemoryMarketDataWorkerStateStore) -> None:
        attempted_at = datetime(2026, 8, 1, tzinfo=UTC)
        await store.record_attempt(
            MarketDataWorkerAttempt(
                provider="demo",
                product_id="BTC-USD",
                timeframe=CandleInterval.ONE_HOUR,
                attempted_at=attempted_at,
                requested_starts_at=attempted_at,
                requested_ends_at=attempted_at,
            )
        )

    store = InMemoryMarketDataWorkerStateStore()
    asyncio.run(seed(store))
    state = asyncio.run(store.get("demo", "BTC-USD", CandleInterval.ONE_HOUR))
    assert state is not None
    object.__setattr__(state, "next_retry_at", datetime.fromisoformat("2026-08-01T00:05:00"))
    app = create_app(Settings(_env_file=None), market_data_state_store=store)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/market-data/ingestion?product_id=BTC-USD")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "market_data_worker_state_unavailable",
            "message": "Market-data ingestion state is unavailable.",
        }
    }


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("status", "forged"),
        ("maintenance_kind", "forged"),
        ("consecutive_failures", -1),
        ("enabled", 1),
    ],
)
def test_ingestion_diagnostics_reject_forged_state_domain_fields(
    field_name: str,
    value: object,
) -> None:
    """Forged lifecycle data must never escape the stable unavailable API envelope."""

    async def seed(store: InMemoryMarketDataWorkerStateStore) -> None:
        attempted_at = datetime(2026, 8, 1, tzinfo=UTC)
        await store.record_attempt(
            MarketDataWorkerAttempt(
                provider="demo",
                product_id="BTC-USD",
                timeframe=CandleInterval.ONE_HOUR,
                attempted_at=attempted_at,
                requested_starts_at=attempted_at,
                requested_ends_at=attempted_at,
            )
        )

    store = InMemoryMarketDataWorkerStateStore()
    asyncio.run(seed(store))
    state = asyncio.run(store.get("demo", "BTC-USD", CandleInterval.ONE_HOUR))
    assert state is not None
    object.__setattr__(state, field_name, value)
    app = create_app(Settings(_env_file=None), market_data_state_store=store)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/market-data/ingestion?product_id=BTC-USD")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "market_data_worker_state_unavailable",
            "message": "Market-data ingestion state is unavailable.",
        }
    }


def test_freshness_endpoint_returns_status_and_age() -> None:
    """GET /api/v1/market-data/freshness returns deterministic status."""
    store = InMemoryMarketDataWorkerStateStore()
    app = create_app(Settings(_env_file=None), market_data_state_store=store)

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/freshness?product_id=BTC-USD")

    assert response.status_code == 200
    data = response.json()
    assert data["product_id"] == "BTC-USD"
    assert data["status"] == "unknown"
    assert data["newest_candle_at"] is None
    assert data["age_seconds"] is None


def test_freshness_endpoint_disabled_store_fails_closed() -> None:
    """Disabled store fails closed with 503."""
    app = create_app(
        Settings(_env_file=None),
        market_data_state_store=DisabledMarketDataWorkerStateStore(),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/market-data/freshness?product_id=BTC-USD")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "market_data_worker_state_unavailable",
            "message": "Market-data ingestion state is unavailable.",
        }
    }


def test_feed_endpoint_returns_snapshot_from_store() -> None:
    """GET /api/v1/market-data/feed returns the latest persisted ticker lifecycle."""

    async def seed() -> InMemoryMarketFeedStateStore:
        store = InMemoryMarketFeedStateStore()
        snapshot = MarketFeedSnapshot(
            product_id="BTC-USD",
            state=MarketFeedState.CONNECTED,
            last_message_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
            last_ticker_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
            last_price=Decimal("65000.50"),
            updated_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
        )
        await store.record(snapshot)
        return store

    store = asyncio.run(seed())
    app = create_app(Settings(_env_file=None), market_feed_state_store=store)

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/feed?product_id=BTC-USD")

    assert response.status_code == 200
    data = response.json()
    assert data["product_id"] == "BTC-USD"
    assert data["state"] == "connected"
    assert data["last_price"] == "65000.50"
    assert data["updated_at"].endswith("Z")


def test_feed_endpoint_defaults_to_disconnected_without_snapshot() -> None:
    """No recorded snapshot yields an honest disconnected response."""
    store = InMemoryMarketFeedStateStore()
    app = create_app(Settings(_env_file=None), market_feed_state_store=store)

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/feed?product_id=BTC-USD")

    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "disconnected"
    assert data["last_price"] is None
    assert data["last_message_at"] is None


def test_feed_endpoint_disabled_store_fails_closed() -> None:
    """Disabled feed store must fail closed with the redacted 503 envelope."""
    app = create_app(
        Settings(_env_file=None),
        market_feed_state_store=DisabledMarketFeedStateStore(),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/market-data/feed?product_id=BTC-USD")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "market_data_worker_state_unavailable",
            "message": "Market-data ingestion state is unavailable.",
        }
    }


def test_feed_endpoint_rejects_forged_snapshot_state() -> None:
    """A hostile store snapshot with an invalid state fails closed 503, not 500."""

    class ForgedSnapshot:
        product_id = "BTC-USD"

        class state:  # noqa: N801 - mimics enum-shaped hostile payload
            value = "evil-state"

        last_message_at = None
        last_ticker_at = None
        last_price = None
        updated_at = datetime(2026, 8, 19, 12, tzinfo=UTC)

    class ForgedStore:
        async def record(self, snapshot: object) -> None:
            del snapshot

        async def get(self, product_id: str) -> object:
            del product_id
            return ForgedSnapshot()

    app = create_app(
        Settings(_env_file=None),
        market_feed_state_store=ForgedStore(),  # type: ignore - hostile test double.
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/market-data/feed?product_id=BTC-USD")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "market_data_worker_state_unavailable",
            "message": "Market-data ingestion state is unavailable.",
        }
    }


def test_feed_endpoint_rejects_forged_naive_datetimes() -> None:
    """Hostile store snapshots with naive datetimes must fail closed 503."""

    async def seed() -> InMemoryMarketFeedStateStore:
        store = InMemoryMarketFeedStateStore()
        snapshot = MarketFeedSnapshot.model_construct(
            product_id="BTC-USD",
            state=MarketFeedState.CONNECTED,
            last_message_at=datetime(2026, 8, 19, 12),  # noqa: DTZ001 - intentionally naive hostile payload
            last_ticker_at=None,
            last_price=None,
            updated_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
        )
        await store.record(snapshot)
        return store

    store = asyncio.run(seed())
    app = create_app(Settings(_env_file=None), market_feed_state_store=store)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/market-data/feed?product_id=BTC-USD")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "market_data_worker_state_unavailable"


def test_freshness_endpoint_rejects_forged_naive_covered_ends_at() -> None:
    """Hostile worker state with naive covered_ends_at must fail closed 503."""

    async def seed() -> InMemoryMarketDataWorkerStateStore:
        store = InMemoryMarketDataWorkerStateStore()
        await store.record_attempt(
            MarketDataWorkerAttempt(
                provider="demo",
                product_id="BTC-USD",
                timeframe=CandleInterval.ONE_HOUR,
                requested_starts_at=datetime(2026, 8, 18, tzinfo=UTC),
                requested_ends_at=datetime(2026, 8, 19, tzinfo=UTC),
                attempted_at=datetime(2026, 8, 19, tzinfo=UTC),
            )
        )
        return store

    store = asyncio.run(seed())
    state = asyncio.run(store.get("demo", "BTC-USD", CandleInterval.ONE_HOUR))
    assert state is not None
    # Intentionally naive: hostile covered_ends_at under test.
    object.__setattr__(state, "covered_ends_at", datetime(2026, 8, 19, 12))  # noqa: DTZ001

    class HostileStateStore(InMemoryMarketDataWorkerStateStore):
        async def get(  # type: ignore - deliberately violates the return contract.
            self, provider: str, product_id: str, timeframe: CandleInterval
        ) -> object:
            del provider, product_id, timeframe
            return state

    app = create_app(
        Settings(_env_file=None),
        market_data_state_store=HostileStateStore(),  # type: ignore - hostile double.
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/market-data/freshness?product_id=BTC-USD")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "market_data_worker_state_unavailable"


def test_feed_endpoint_rejects_forged_zero_offset_non_utc_datetime() -> None:
    """A named zero-offset timezone cannot be presented as UTC ticker evidence."""

    async def seed() -> InMemoryMarketFeedStateStore:
        store = InMemoryMarketFeedStateStore()
        snapshot = MarketFeedSnapshot.model_construct(
            product_id="BTC-USD",
            state=MarketFeedState.CONNECTED,
            last_message_at=datetime(
                2026, 8, 19, 12, tzinfo=timezone(timedelta(0), "forged-zero-offset-zone")
            ),
            last_ticker_at=None,
            last_price=None,
            updated_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
        )
        await store.record(snapshot)
        return store

    app = create_app(Settings(_env_file=None), market_feed_state_store=asyncio.run(seed()))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/market-data/feed?product_id=BTC-USD")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "market_data_worker_state_unavailable"


def test_feed_endpoint_rejects_forged_nan_price() -> None:
    """A hostile feed snapshot cannot serialize a non-finite Decimal price."""
    snapshot = MarketFeedSnapshot.model_construct(
        product_id="BTC-USD",
        state=MarketFeedState.CONNECTED,
        last_message_at=None,
        last_ticker_at=None,
        last_price=Decimal("NaN"),
        updated_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
    )

    class HostileStore(InMemoryMarketFeedStateStore):
        async def get(self, product_id: str) -> MarketFeedSnapshot | None:
            del product_id
            return snapshot

    app = create_app(Settings(_env_file=None), market_feed_state_store=HostileStore())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/market-data/feed?product_id=BTC-USD")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "market_data_worker_state_unavailable"


def test_feed_endpoint_rejects_snapshot_for_another_product() -> None:
    """A store result for ETH cannot be returned for a BTC request."""
    snapshot = MarketFeedSnapshot.model_construct(
        product_id="ETH-USD",
        state=MarketFeedState.CONNECTED,
        last_message_at=None,
        last_ticker_at=None,
        last_price=Decimal("3000"),
        updated_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
    )

    class HostileStore(InMemoryMarketFeedStateStore):
        async def get(self, product_id: str) -> MarketFeedSnapshot | None:
            del product_id
            return snapshot

    app = create_app(Settings(_env_file=None), market_feed_state_store=HostileStore())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/market-data/feed?product_id=BTC-USD")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "market_data_worker_state_unavailable"
