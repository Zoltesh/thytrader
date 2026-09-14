"""HTTP contracts for watchlist ingest and gap inspection."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.market_data.watchlist import InMemoryMarketDataWatchlistStore
from thytrader.market_data.worker_state import InMemoryMarketDataWorkerStateStore
from thytrader.market_data_worker.service import run_market_data_worker
from thytrader.operator.models import SCHEMA_VERSION
from thytrader.persistence.audit_events import InMemoryAuditEventStore

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI


def _client(tmp_path: Path) -> TestClient:
    """Build an API with in-memory watchlist, worker state, and an isolated dataset root."""
    app = create_app(
        Settings(_env_file=None, market_data_dataset_root=tmp_path),
        market_data_watchlist_store=InMemoryMarketDataWatchlistStore(),
        market_data_state_store=InMemoryMarketDataWorkerStateStore(),
        audit_event_store=InMemoryAuditEventStore(),
    )
    return TestClient(app)


async def _run_worker_cycle(app: FastAPI) -> None:
    """Let the dedicated worker consume one queued ingest request."""
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_market_data_worker(
            stop,
            service=app.state.market_data_service,
            dataset_store=app.state.dataset_store,
            state_store=app.state.market_data_state_store,
            provider="demo",
            product_id="ETH-USD",
            lookback_hours=1,
            interval_seconds=1,
            watchlist=app.state.market_data_watchlist_store,
        )
    )
    await asyncio.sleep(0.3)
    stop.set()
    await task


def test_watch_add_and_ingest_five_minute_demo_range(tmp_path: Path) -> None:
    """A complete 14-day island is not done when its watch grows to 90 days."""
    app = create_app(
        Settings(_env_file=None, market_data_dataset_root=tmp_path),
        market_data_watchlist_store=InMemoryMarketDataWatchlistStore(),
        market_data_state_store=InMemoryMarketDataWorkerStateStore(),
        audit_event_store=InMemoryAuditEventStore(),
    )
    with TestClient(app) as client:
        added = client.put(
            "/api/v1/data/watchlist",
            json={
                "product_id": "ETH-USD",
                "timeframe": "5m",
                "lookback_hours": 336,
                "enabled": True,
            },
        )
        ingest = client.post(
            "/api/v1/data/ingest",
            json={"product_id": "ETH-USD", "timeframe": "5m"},
        )
        assert added.status_code == 200, added.text
        assert ingest.status_code == 202, ingest.text
        assert ingest.json()["accepted"] is True
        assert ingest.json()["ingest_requested_at"] is not None
        asyncio.run(_run_worker_cycle(app))
        status = client.get("/api/v1/data/ingest?product_id=ETH-USD&timeframe=5m")
        catalog = client.get("/api/v1/operator/data-catalog")
        indicators = client.get("/api/v1/operator/indicators")
        products = client.get("/api/v1/operator/products")
        rows = catalog.json()["payload"]["datasets"]
        eth_five = next(
            row for row in rows if row["product_id"] == "ETH-USD" and row["timeframe"] == "5m"
        )
        assert eth_five["complete"] is True
        assert eth_five["watch_complete"] is True
        assert eth_five["sparsity"] == "none"
        assert status.json()["state"]["watch_complete"] is True
        assert eth_five["expected_candle_count"] == 4_032
        covered_start = datetime.fromisoformat(eth_five["covered_starts_at"])
        covered_end = datetime.fromisoformat(eth_five["covered_ends_at"])
        assert covered_end - covered_start == timedelta(hours=336)
        lengthened = client.put(
            "/api/v1/data/watchlist",
            json={
                "product_id": "ETH-USD",
                "timeframe": "5m",
                "lookback_hours": 2160,
                "enabled": True,
            },
        )
        assert lengthened.status_code == 200, lengthened.text
        catalog_after = client.get("/api/v1/operator/data-catalog")
        longer = next(
            row
            for row in catalog_after.json()["payload"]["datasets"]
            if row["product_id"] == "ETH-USD" and row["timeframe"] == "5m"
        )
        assert longer["complete"] is True
        assert longer["watch_complete"] is False
        assert longer["sparsity"] == "gapped"
        assert longer["watch_expected_candle_count"] > longer["expected_candle_count"]
        gap_report = client.get("/api/v1/data/gaps?product_id=ETH-USD&timeframe=5m")
        assert gap_report.status_code == 200, gap_report.text
        gaps = gap_report.json()
        assert gaps["complete"] is True
        assert gaps["watch_complete"] is False
        assert gaps["lookback_hours"] == 2_160
        assert gaps["gap_count"] > 0
        assert {item["cause"] for item in gaps["gaps"]} <= {
            "not_fetched",
            "exchange_unavailable",
            "incomplete_local",
        }
        assert gaps["interpolated"] is False
        assert datetime.fromisoformat(gaps["gaps"][0]["starts_at"]) < covered_start
        assert list(longer).index("watch_complete") < list(longer).index("complete")
        assert list(gaps).index("watch_complete") < list(gaps).index("complete")

    assert added.json()["target"]["product_id"] == "ETH-USD"
    assert added.json()["target"]["timeframe"] == "5m"
    assert status.status_code == 200, status.text
    assert status.json()["ingest_requested_at"] is None
    assert status.json()["state"]["complete"] is True
    assert list(status.json()["state"]).index("watch_complete") < list(
        status.json()["state"]
    ).index("complete")
    assert status.json()["state"]["status"] == "succeeded"
    assert catalog.status_code == 200
    assert catalog.json()["schema_version"] == SCHEMA_VERSION
    assert catalog.json()["report_kind"] == "data_catalog"
    kinds = {item["kind"] for item in indicators.json()["payload"]["indicators"]}
    assert kinds == {
        "ema",
        "sma",
        "rsi",
        "atr",
        "volume_sma",
        "highest",
        "lowest",
        "stdev",
    }
    product_ids = {item["product_id"] for item in products.json()["payload"]["products"]}
    assert "ETH-USD" in product_ids


def test_watch_add_and_ingest_fifteen_minute_demo_range(tmp_path: Path) -> None:
    """Agents can watch ETH 15m; the worker publishes a complete demo range."""
    app = create_app(
        Settings(_env_file=None, market_data_dataset_root=tmp_path),
        market_data_watchlist_store=InMemoryMarketDataWatchlistStore(),
        market_data_state_store=InMemoryMarketDataWorkerStateStore(),
        audit_event_store=InMemoryAuditEventStore(),
    )
    with TestClient(app) as client:
        added = client.put(
            "/api/v1/data/watchlist",
            json={
                "product_id": "ETH-USD",
                "timeframe": "15m",
                "lookback_hours": 1,
                "enabled": True,
            },
        )
        ingest = client.post(
            "/api/v1/data/ingest",
            json={"product_id": "ETH-USD", "timeframe": "15m"},
        )
        assert added.status_code == 200, added.text
        assert ingest.status_code == 202, ingest.text
        asyncio.run(_run_worker_cycle(app))
        status = client.get("/api/v1/data/ingest?product_id=ETH-USD&timeframe=15m")
        catalog = client.get("/api/v1/operator/data-catalog")
        latest = client.get("/api/v1/market-data/datasets/latest")
        rows = catalog.json()["payload"]["datasets"]
        eth_fifteen = next(
            row for row in rows if row["product_id"] == "ETH-USD" and row["timeframe"] == "15m"
        )
        assert eth_fifteen["complete"] is True
        assert eth_fifteen["watch_complete"] is True
        assert catalog.json()["payload"]["supported_timeframes"] == [
            "1h",
            "5m",
            "15m",
            "30m",
            "6h",
            "1d",
        ]
        assert any(item["timeframe"] == "15m" for item in latest.json()["datasets"])
        rejected = client.put(
            "/api/v1/data/watchlist",
            json={"product_id": "ETH-USD", "timeframe": "2d", "lookback_hours": 1},
        )
        assert rejected.status_code == 422

    assert added.json()["target"]["timeframe"] == "15m"
    assert status.status_code == 200, status.text
    assert status.json()["ingest_requested_at"] is None
    assert status.json()["state"]["complete"] is True
    assert status.json()["state"]["status"] == "succeeded"


def test_watch_add_and_ingest_thirty_minute_demo_range(tmp_path: Path) -> None:
    """Agents can watch ETH 30m; the worker publishes a complete demo range."""
    app = create_app(
        Settings(_env_file=None, market_data_dataset_root=tmp_path),
        market_data_watchlist_store=InMemoryMarketDataWatchlistStore(),
        market_data_state_store=InMemoryMarketDataWorkerStateStore(),
        audit_event_store=InMemoryAuditEventStore(),
    )
    with TestClient(app) as client:
        added = client.put(
            "/api/v1/data/watchlist",
            json={
                "product_id": "ETH-USD",
                "timeframe": "30m",
                "lookback_hours": 1,
                "enabled": True,
            },
        )
        ingest = client.post(
            "/api/v1/data/ingest",
            json={"product_id": "ETH-USD", "timeframe": "30m"},
        )
        assert added.status_code == 200, added.text
        assert ingest.status_code == 202, ingest.text
        asyncio.run(_run_worker_cycle(app))
        status = client.get("/api/v1/data/ingest?product_id=ETH-USD&timeframe=30m")
        catalog = client.get("/api/v1/operator/data-catalog")
        latest = client.get("/api/v1/market-data/datasets/latest")
        rows = catalog.json()["payload"]["datasets"]
        eth_thirty = next(
            row for row in rows if row["product_id"] == "ETH-USD" and row["timeframe"] == "30m"
        )
        assert eth_thirty["complete"] is True
        assert eth_thirty["watch_complete"] is True
        assert catalog.json()["payload"]["supported_timeframes"] == [
            "1h",
            "5m",
            "15m",
            "30m",
            "6h",
            "1d",
        ]
        assert any(item["timeframe"] == "30m" for item in latest.json()["datasets"])
        rejected = client.put(
            "/api/v1/data/watchlist",
            json={"product_id": "ETH-USD", "timeframe": "2d", "lookback_hours": 1},
        )
        assert rejected.status_code == 422

    assert added.json()["target"]["timeframe"] == "30m"
    assert status.status_code == 200, status.text
    assert status.json()["ingest_requested_at"] is None
    assert status.json()["state"]["complete"] is True
    assert status.json()["state"]["status"] == "succeeded"


def test_watch_add_and_ingest_six_hour_demo_range(tmp_path: Path) -> None:
    """Agents can watch ETH 6h; the worker publishes a complete demo range."""
    app = create_app(
        Settings(_env_file=None, market_data_dataset_root=tmp_path),
        market_data_watchlist_store=InMemoryMarketDataWatchlistStore(),
        market_data_state_store=InMemoryMarketDataWorkerStateStore(),
        audit_event_store=InMemoryAuditEventStore(),
    )
    with TestClient(app) as client:
        added = client.put(
            "/api/v1/data/watchlist",
            json={
                "product_id": "ETH-USD",
                "timeframe": "6h",
                "lookback_hours": 24,
                "enabled": True,
            },
        )
        ingest = client.post(
            "/api/v1/data/ingest",
            json={"product_id": "ETH-USD", "timeframe": "6h"},
        )
        assert added.status_code == 200, added.text
        assert ingest.status_code == 202, ingest.text
        asyncio.run(_run_worker_cycle(app))
        status = client.get("/api/v1/data/ingest?product_id=ETH-USD&timeframe=6h")
        catalog = client.get("/api/v1/operator/data-catalog")
        latest = client.get("/api/v1/market-data/datasets/latest")
        rows = catalog.json()["payload"]["datasets"]
        eth_six = next(
            row for row in rows if row["product_id"] == "ETH-USD" and row["timeframe"] == "6h"
        )
        assert eth_six["complete"] is True
        assert eth_six["watch_complete"] is True
        assert catalog.json()["payload"]["supported_timeframes"] == [
            "1h",
            "5m",
            "15m",
            "30m",
            "6h",
            "1d",
        ]
        assert any(item["timeframe"] == "6h" for item in latest.json()["datasets"])
        rejected = client.put(
            "/api/v1/data/watchlist",
            json={"product_id": "ETH-USD", "timeframe": "2d", "lookback_hours": 1},
        )
        assert rejected.status_code == 422

    assert added.json()["target"]["timeframe"] == "6h"
    assert status.status_code == 200, status.text
    assert status.json()["ingest_requested_at"] is None
    assert status.json()["state"]["complete"] is True
    assert status.json()["state"]["status"] == "succeeded"


def test_watch_add_and_ingest_one_day_demo_range(tmp_path: Path) -> None:
    """Agents can watch ETH 1d; the worker publishes a complete demo range."""
    app = create_app(
        Settings(_env_file=None, market_data_dataset_root=tmp_path),
        market_data_watchlist_store=InMemoryMarketDataWatchlistStore(),
        market_data_state_store=InMemoryMarketDataWorkerStateStore(),
        audit_event_store=InMemoryAuditEventStore(),
    )
    with TestClient(app) as client:
        added = client.put(
            "/api/v1/data/watchlist",
            json={
                "product_id": "ETH-USD",
                "timeframe": "1d",
                "lookback_hours": 24,
                "enabled": True,
            },
        )
        ingest = client.post(
            "/api/v1/data/ingest",
            json={"product_id": "ETH-USD", "timeframe": "1d"},
        )
        assert added.status_code == 200, added.text
        assert ingest.status_code == 202, ingest.text
        asyncio.run(_run_worker_cycle(app))
        status = client.get("/api/v1/data/ingest?product_id=ETH-USD&timeframe=1d")
        catalog = client.get("/api/v1/operator/data-catalog")
        latest = client.get("/api/v1/market-data/datasets/latest")
        rows = catalog.json()["payload"]["datasets"]
        eth_day = next(
            row for row in rows if row["product_id"] == "ETH-USD" and row["timeframe"] == "1d"
        )
        assert eth_day["complete"] is True
        assert eth_day["watch_complete"] is True
        assert catalog.json()["payload"]["supported_timeframes"] == [
            "1h",
            "5m",
            "15m",
            "30m",
            "6h",
            "1d",
        ]
        assert any(item["timeframe"] == "1d" for item in latest.json()["datasets"])
        rejected = client.put(
            "/api/v1/data/watchlist",
            json={"product_id": "ETH-USD", "timeframe": "2d", "lookback_hours": 1},
        )
        assert rejected.status_code == 422

    assert added.json()["target"]["timeframe"] == "1d"
    assert status.status_code == 200, status.text
    assert status.json()["ingest_requested_at"] is None
    assert status.json()["state"]["complete"] is True
    assert status.json()["state"]["status"] == "succeeded"


def test_inspect_gaps_does_not_interpolate(tmp_path: Path) -> None:
    """Gap inspection reports cause codes and never claims interpolation."""
    with _client(tmp_path) as client:
        five = client.get("/api/v1/data/gaps?product_id=ETH-USD&timeframe=5m")
        fifteen = client.get("/api/v1/data/gaps?product_id=ETH-USD&timeframe=15m")
        thirty = client.get("/api/v1/data/gaps?product_id=ETH-USD&timeframe=30m")
        six_hour = client.get("/api/v1/data/gaps?product_id=ETH-USD&timeframe=6h")
        one_day = client.get("/api/v1/data/gaps?product_id=ETH-USD&timeframe=1d")
    assert five.status_code == 200, five.text
    assert fifteen.status_code == 200, fifteen.text
    assert thirty.status_code == 200, thirty.text
    assert six_hour.status_code == 200, six_hour.text
    assert one_day.status_code == 200, one_day.text
    for response in (five, fifteen, thirty, six_hour, one_day):
        body = response.json()
        assert body["interpolated"] is False
        assert datetime.fromisoformat(body["starts_at"]) < datetime.fromisoformat(body["ends_at"])
        assert body["ends_at"].endswith("+00:00") or body["ends_at"].endswith("Z")
    assert five.json()["timeframe"] == "5m"
    assert fifteen.json()["timeframe"] == "15m"
    assert thirty.json()["timeframe"] == "30m"
    assert six_hour.json()["timeframe"] == "6h"
    assert one_day.json()["timeframe"] == "1d"


def test_unknown_product_watch_is_rejected(tmp_path: Path) -> None:
    """Watch-add must not accept products outside the USD spot catalog."""
    with _client(tmp_path) as client:
        response = client.put(
            "/api/v1/data/watchlist",
            json={"product_id": "ZZZ-USD", "timeframe": "1h", "lookback_hours": 24},
        )
    assert response.status_code == 400
