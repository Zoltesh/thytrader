"""Behavioral tests for the read-only portfolio valuation history API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.persistence.portfolio_history import InMemoryPortfolioHistoryStore

if TYPE_CHECKING:
    from thytrader.portfolio.models import Portfolio


class _CountingHistoryStore(InMemoryPortfolioHistoryStore):
    """In-memory store that counts successful record attempts."""

    def __init__(self) -> None:
        """Initialize with zero recorded snapshots."""
        super().__init__()
        self.record_count = 0

    async def record(self, portfolio: Portfolio) -> None:
        """Count and retain each successful portfolio observation."""
        self.record_count += 1
        await super().record(portfolio)


class _FailingHistoryStore(InMemoryPortfolioHistoryStore):
    """Store that fails on record to test redacted 503 behavior."""

    async def record(self, portfolio: Portfolio) -> None:
        """Raise a synthetic persistence failure."""
        del portfolio
        raise RuntimeError("synthetic database failure")


def test_portfolio_refresh_appends_history_with_decimal_strings() -> None:
    """A complete portfolio response becomes one exact history observation."""
    store = _CountingHistoryStore()
    app = create_app(Settings(_env_file=None), history_store=store)

    with TestClient(app) as client:
        refresh = client.get("/api/v1/portfolio")
        history = client.get("/api/v1/portfolio/history?limit=5")

    assert refresh.status_code == 200
    assert history.status_code == 200
    assert store.record_count == 1
    entries = history.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["as_of"] == refresh.json()["as_of"]
    assert entries[0]["total_value"] == refresh.json()["total_value"]


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


def test_failed_persistence_write_returns_redacted_503() -> None:
    """A persistence failure must not expose internals or claim a successful refresh."""
    app = create_app(Settings(_env_file=None), history_store=_FailingHistoryStore())

    with TestClient(app) as client:
        response = client.get("/api/v1/portfolio")

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "persistence_unavailable"
    assert "synthetic" not in detail["message"]


def test_history_limit_is_bounded() -> None:
    """History requests reject unbounded resource use."""
    app = create_app(Settings(_env_file=None), history_store=_CountingHistoryStore())

    with TestClient(app) as client:
        response = client.get("/api/v1/portfolio/history?limit=201")

    assert response.status_code == 422


def test_history_limit_minimum_is_enforced() -> None:
    """A zero or negative limit must be rejected by validation."""
    app = create_app(Settings(_env_file=None), history_store=_CountingHistoryStore())

    with TestClient(app) as client:
        response = client.get("/api/v1/portfolio/history?limit=0")

    assert response.status_code == 422


def test_multiple_refreshes_produce_newest_first_history() -> None:
    """Repeated refreshes append observations and list newest first."""
    store = _CountingHistoryStore()
    app = create_app(Settings(_env_file=None), history_store=store)

    with TestClient(app) as client:
        first = client.get("/api/v1/portfolio")
        second = client.get("/api/v1/portfolio")
        history = client.get("/api/v1/portfolio/history?limit=10")

    assert first.status_code == 200
    assert second.status_code == 200
    entries = history.json()["entries"]
    assert len(entries) == 2
    assert entries[0]["as_of"] == second.json()["as_of"]
    assert entries[1]["as_of"] == first.json()["as_of"]
