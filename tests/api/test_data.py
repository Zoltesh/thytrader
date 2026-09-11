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
    """Agents can watch ETH 5m; the worker publishes a complete demo range."""
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
                "lookback_hours": 1,
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

    assert added.json()["target"]["product_id"] == "ETH-USD"
    assert added.json()["target"]["timeframe"] == "5m"
    assert status.status_code == 200, status.text
    assert status.json()["ingest_requested_at"] is None
    assert status.json()["state"]["complete"] is True
    assert status.json()["state"]["status"] == "succeeded"
    assert catalog.status_code == 200
    assert catalog.json()["schema_version"] == SCHEMA_VERSION
    assert catalog.json()["report_kind"] == "data_catalog"
    rows = catalog.json()["payload"]["datasets"]
    assert any(
        row["product_id"] == "ETH-USD" and row["timeframe"] == "5m" and row["complete"] is True
        for row in rows
    )
    kinds = {item["kind"] for item in indicators.json()["payload"]["indicators"]}
    assert kinds == {"ema", "sma", "rsi", "atr", "volume_sma"}
    product_ids = {item["product_id"] for item in products.json()["payload"]["products"]}
    assert "ETH-USD" in product_ids


def test_inspect_gaps_does_not_interpolate(tmp_path: Path) -> None:
    """Gap inspection reports cause codes and never claims interpolation."""
    with _client(tmp_path) as client:
        response = client.get("/api/v1/data/gaps?product_id=ETH-USD&timeframe=5m")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["interpolated"] is False
    assert body["timeframe"] == "5m"
    assert datetime.fromisoformat(body["starts_at"]) < datetime.fromisoformat(body["ends_at"])
    assert body["ends_at"].endswith("+00:00") or body["ends_at"].endswith("Z")
    assert timedelta(hours=1) <= (
        datetime.fromisoformat(body["ends_at"]) - datetime.fromisoformat(body["starts_at"])
    )


def test_unknown_product_watch_is_rejected(tmp_path: Path) -> None:
    """Watch-add must not accept products outside the USD spot catalog."""
    with _client(tmp_path) as client:
        response = client.put(
            "/api/v1/data/watchlist",
            json={"product_id": "ZZZ-USD", "timeframe": "1h", "lookback_hours": 24},
        )
    assert response.status_code == 400
