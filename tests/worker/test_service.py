"""Behavioral tests for the continuously running ThyTrader worker."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from thytrader.config import Settings
from thytrader.persistence.audit_events import InMemoryAuditEventStore
from thytrader.persistence.portfolio_history import InMemoryPortfolioHistoryStore
from thytrader.portfolio.models import (
    Money,
    Portfolio,
    PortfolioAsset,
    PortfolioConnection,
)
from thytrader.runtime import RuntimeState
from thytrader.worker.service import run_worker


class _StubPortfolioService:
    """Deterministic portfolio service for worker tests."""

    def __init__(self, *, demo: bool = False) -> None:
        self._demo = demo
        self.call_count = 0

    async def get_portfolio(self) -> Portfolio:
        self.call_count += 1
        return Portfolio(
            as_of=datetime.now(UTC),
            connection=PortfolioConnection(
                provider="coinbase",
                status="demo" if self._demo else "connected",
                permissions=("view", "trade"),
            ),
            demo=self._demo,
            total_value=Money(amount=Decimal("100000.00")),
            assets=(
                PortfolioAsset(
                    currency="BTC",
                    name="Bitcoin",
                    available=Decimal("1"),
                    hold=Decimal("0"),
                    total=Decimal("1"),
                    value=Money(amount=Decimal("100000.00")),
                ),
            ),
            unvalued_assets=(),
        )


def test_worker_takes_initial_snapshot_then_stops() -> None:
    """Worker records one snapshot immediately on startup."""

    async def exercise() -> None:
        settings = Settings(_env_file=None, snapshot_interval_seconds=60)
        runtime = RuntimeState(settings=settings)
        stop = asyncio.Event()
        service = _StubPortfolioService(demo=False)
        store = InMemoryPortfolioHistoryStore()

        task = asyncio.create_task(
            run_worker(
                runtime,
                stop,
                portfolio_service=service,  # type: ignore[arg-type]
                history_store=store,
            )
        )
        await asyncio.sleep(0.05)
        assert service.call_count == 1
        stop.set()
        await task
        assert runtime.ready is False

    asyncio.run(exercise())


def test_worker_records_audit_events_on_lifecycle_and_snapshots() -> None:
    """Worker records worker_started, snapshot, and worker_stopped audit events."""

    async def exercise() -> None:
        settings = Settings(_env_file=None, snapshot_interval_seconds=60)
        runtime = RuntimeState(settings=settings)
        stop = asyncio.Event()
        service = _StubPortfolioService(demo=False)
        history_store = InMemoryPortfolioHistoryStore()
        audit_store = InMemoryAuditEventStore()

        task = asyncio.create_task(
            run_worker(
                runtime,
                stop,
                portfolio_service=service,  # type: ignore[arg-type]
                history_store=history_store,
                audit_store=audit_store,
            )
        )
        await asyncio.sleep(0.05)
        stop.set()
        await task

        events = await audit_store.list_recent(limit=10)
        assert len(events) == 3
        actions = [e.action for e in events]
        assert actions == ["worker_stopped", "portfolio_snapshot_recorded", "worker_started"]

    asyncio.run(exercise())


def test_worker_skips_demo_snapshots() -> None:
    """Demo portfolios are not recorded to avoid meaningless flat history."""

    async def exercise() -> None:
        settings = Settings(_env_file=None, snapshot_interval_seconds=60)
        runtime = RuntimeState(settings=settings)
        stop = asyncio.Event()
        service = _StubPortfolioService(demo=True)
        store = InMemoryPortfolioHistoryStore()

        task = asyncio.create_task(
            run_worker(
                runtime,
                stop,
                portfolio_service=service,  # type: ignore[arg-type]
                history_store=store,
            )
        )
        await asyncio.sleep(0.05)
        stop.set()
        await task

        entries = await store.list_range(start=None, max_entries=10)
        assert len(entries) == 0

    asyncio.run(exercise())


def test_worker_runs_readiness_callback_before_snapshot_collection() -> None:
    """Supervisors can observe readiness before the worker enters its wait loop."""

    async def exercise() -> None:
        runtime = RuntimeState(settings=Settings(_env_file=None))
        stop = asyncio.Event()
        started = False

        def mark_started() -> None:
            """Record that the worker's run loop is available."""
            nonlocal started
            started = True

        task = asyncio.create_task(
            run_worker(
                runtime,
                stop,
                portfolio_service=_StubPortfolioService(),  # type: ignore[arg-type]
                on_started=mark_started,
            )
        )
        await asyncio.sleep(0)
        assert started is True
        stop.set()
        await task

    asyncio.run(exercise())


def test_worker_readiness_tracks_its_running_lifecycle() -> None:
    """Worker readiness is true only while the run loop is active."""

    async def exercise() -> None:
        settings = Settings(_env_file=None)
        runtime = RuntimeState(settings=settings)
        stop = asyncio.Event()
        service = _StubPortfolioService()

        task = asyncio.create_task(
            run_worker(
                runtime,
                stop,
                portfolio_service=service,  # type: ignore[arg-type]
            )
        )
        await asyncio.sleep(0)
        assert runtime.ready is True
        stop.set()
        await task
        assert runtime.ready is False

    asyncio.run(exercise())
