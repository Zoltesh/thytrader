"""Behavioral tests for the read-only portfolio valuation history API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.persistence.portfolio_history import InMemoryPortfolioHistoryStore


def test_disabled_persistence_does_not_break_portfolio_refresh() -> None:
    """Absent database config must not prevent normal demo portfolio operation."""
    app = create_app(Settings(_env_file=None))

    with TestClient(app) as client:
        response = client.get("/api/v1/portfolio")

    assert response.status_code == 200


def test_history_reports_unavailable_when_persistence_is_disabled() -> None:
    """Disabled durable storage must not be represented as an empty history series."""
    app = create_app(Settings(_env_file=None))

    with TestClient(app) as client:
        response = client.get("/api/v1/portfolio/history")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "persistence_unavailable"


def test_history_returns_entries_when_store_is_configured() -> None:
    """A configured store returns saved entries through the read-only history API."""
    store = InMemoryPortfolioHistoryStore()
    app = create_app(Settings(_env_file=None), history_store=store)

    with TestClient(app) as client:
        history = client.get("/api/v1/portfolio/history?limit=5")

    assert history.status_code == 200
    assert "entries" in history.json()


def test_history_limit_is_bounded() -> None:
    """History requests reject unbounded resource use."""
    app = create_app(Settings(_env_file=None), history_store=InMemoryPortfolioHistoryStore())

    with TestClient(app) as client:
        response = client.get("/api/v1/portfolio/history?limit=201")

    assert response.status_code == 422


def test_history_limit_minimum_is_enforced() -> None:
    """A zero or negative limit must be rejected by validation."""
    app = create_app(Settings(_env_file=None), history_store=InMemoryPortfolioHistoryStore())

    with TestClient(app) as client:
        response = client.get("/api/v1/portfolio/history?limit=0")

    assert response.status_code == 422


def test_portfolio_refresh_does_not_record_snapshots() -> None:
    """Refresh is read-only: it must not create fake history points."""
    store = InMemoryPortfolioHistoryStore()
    app = create_app(Settings(_env_file=None), history_store=store)

    with TestClient(app) as client:
        client.get("/api/v1/portfolio")
        history = client.get("/api/v1/portfolio/history?limit=10")

    assert history.status_code == 200
    assert len(history.json()["entries"]) == 0
