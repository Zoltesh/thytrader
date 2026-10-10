"""Futures ids reach data surfaces only; every order surface refuses them (ADR 0126)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
import pytest

from thytrader.api.app import create_app
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.config import Settings
from thytrader.market_data.watchlist import InMemoryMarketDataWatchlistStore
from thytrader.market_data.worker_state import InMemoryMarketDataWorkerStateStore

if TYPE_CHECKING:
    from pathlib import Path

_FUTURE = "BIP-20DEC30-CDE"


def _client(tmp_path: Path) -> TestClient:
    """Demo API with in-memory data stores."""
    app = create_app(
        Settings(_env_file=None, market_data_dataset_root=tmp_path),
        market_data_watchlist_store=InMemoryMarketDataWatchlistStore(),
        market_data_state_store=InMemoryMarketDataWorkerStateStore(),
        audit_event_store=InMemoryAuditEventStore(),
    )
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "POST",
            "/api/v1/discretionary-orders",
            {
                "mode": "paper",
                "product_id": _FUTURE,
                "stop_price": "1",
                "take_profit_price": "2",
                "origin": "agent",
                "idempotency_key": "futures-refused",
            },
        ),
        (
            "POST",
            "/api/v1/inventory-adoptions",
            {
                "mode": "live",
                "action": "protect",
                "product_id": _FUTURE,
                "quantity": "1",
                "idempotency_key": "futures-refused",
                "origin": "agent",
            },
        ),
        ("GET", f"/api/v1/inventory-adoptions/preview?product_id={_FUTURE}", None),
    ],
)
def test_order_and_adoption_surfaces_refuse_futures_ids(
    tmp_path: Path, method: str, path: str, body: dict[str, object] | None
) -> None:
    """The spot-only pattern rejects a futures id with 422 before any handler runs."""
    with _client(tmp_path) as client:
        response = client.request(method, path, json=body)
    assert response.status_code == 422
    assert "product_id" in response.text


def test_data_surfaces_accept_the_futures_id_pattern(tmp_path: Path) -> None:
    """The data lane parses a futures id; demo mode then refuses it as not enabled (400)."""
    with _client(tmp_path) as client:
        watch = client.put(
            "/api/v1/data/watchlist",
            json={"product_id": _FUTURE, "timeframe": "1h", "lookback_hours": 24},
        )
        status = client.get(f"/api/v1/data/ingest?product_id={_FUTURE}&timeframe=1h")
        freshness = client.get(f"/api/v1/market-data/freshness?product_id={_FUTURE}")
        report = client.get(f"/api/v1/operator/market-data?product_id={_FUTURE}")
    assert watch.status_code == 400
    assert "not an enabled Coinbase futures contract" in watch.text
    assert status.status_code != 422
    assert freshness.status_code != 422
    assert report.status_code == 200
