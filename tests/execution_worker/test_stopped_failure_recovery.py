"""A worker failure on a stopped book resolves once its maintenance pass completes (ADR 0115).

A stopped book makes no decisions, so its decision cursor never advances and the decision rule
alone left such a failure open forever.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from thytrader.alerts.models import AlertCode
from thytrader.alerts.service import AlertService
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.alerts.supervision_inputs import AlertThresholds
from thytrader.alerts.supervision_rows import STOPPED_MAINTENANCE_RECOVERY
from thytrader.execution.leases import acquire_worker_lease
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker import service as worker_service
from thytrader.execution_worker.cycle_timing import BookProbe, CycleTimer
from thytrader.execution_worker.service import _process_one, _run_cycle
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.memory.notify import DisabledNotificationSender
from thytrader.risk.store import load_effective_policy
from thytrader.strategies.memory_store import InMemoryStrategyStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from thytrader.alerts.models import OperatorAlert

pytestmark = pytest.mark.anyio
_NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
_PAUSE = "WORKER_CONSECUTIVE_FAILURES: entries paused after 3 failed supervision cycles."


def _book(
    *, status: DeploymentStatus = DeploymentStatus.STOPPED, mismatch: str | None = None
) -> Deployment:
    """One flat discretionary paper book in ``status``."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint=None,
        strategy_id=None,
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=status,
        cash=Decimal("1000"),
        phase=RuntimePhase.FLAT,
        created_at=_NOW - timedelta(days=1),
        updated_at=_NOW,
        kind=DeploymentKind.DISCRETIONARY,
        timeframe="1h",
        mismatch_detail=mismatch,
    )


def _alerts() -> tuple[InMemoryAlertStore, AlertService]:
    store = InMemoryAlertStore()
    service = AlertService(
        store,
        DisabledNotificationSender(),
        thresholds=AlertThresholds(consecutive_failure_cycles=3, delivery_max_attempts=2),
    )
    return store, service


async def _cycle(execution: InMemoryExecutionStore, alerts: AlertService) -> None:
    await _run_cycle(
        store=execution,
        publication_store=InMemoryStrategyStore(),
        market_data=MarketDataService(DemoMarketData()),
        paper_broker=PaperBroker(),
        live_broker=None,
        quote_reader=None,
        risk_store=None,
        alert_service=alerts,
        worker_interval_seconds=30,
    )


def _passes(*outcomes: bool | Exception) -> Callable[..., Awaitable[bool]]:
    """A ``_process_one`` stand-in that raises or returns each outcome in turn."""
    queue = list(outcomes)

    async def process(**kwargs: object) -> bool:
        del kwargs
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return process


async def _failure_rows(feed: InMemoryAlertStore, book: Deployment) -> list[OperatorAlert]:
    return [
        row
        for rows in feed.history.values()
        for row in rows
        if row.code is AlertCode.WORKER_BOOK_FAILURES and row.subject == str(book.id)
    ]


async def test_a_completed_maintenance_pass_resolves_a_stopped_books_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure closes with a note saying which work proved it, not the generic one."""
    monkeypatch.setattr(
        worker_service, "_process_one", _passes(RuntimeError("revision conflict"), True)
    )
    execution = InMemoryExecutionStore()
    book = _book()
    await execution.create_deployment(book)
    feed, alerts = _alerts()
    await _cycle(execution, alerts)
    (opened,) = await _failure_rows(feed, book)
    assert opened.is_open
    await _cycle(execution, alerts)
    (row,) = await _failure_rows(feed, book)
    assert not row.is_open
    assert row.resolution_detail == STOPPED_MAINTENANCE_RECOVERY


@pytest.mark.parametrize(
    ("status", "mismatch"),
    [
        # A lease-skipped or deferred stopped book proved nothing.
        (DeploymentStatus.STOPPED, None),
        # Running and paused books recover only by advancing their decision cursor.
        (DeploymentStatus.RUNNING, None),
        (DeploymentStatus.PAUSED, None),
        # A persisted supervision pause keeps its evidence until manual clearing.
        (DeploymentStatus.STOPPED, _PAUSE),
    ],
)
async def test_other_passes_leave_the_failure_open(
    monkeypatch: pytest.MonkeyPatch, status: DeploymentStatus, mismatch: str | None
) -> None:
    """Only a completed pass of a book stopped before and after it clears the failure."""
    completed = status is not DeploymentStatus.STOPPED or mismatch is not None
    monkeypatch.setattr(
        worker_service, "_process_one", _passes(RuntimeError("boom"), completed, completed)
    )
    execution = InMemoryExecutionStore()
    book = _book(status=status, mismatch=mismatch)
    await execution.create_deployment(book)
    feed, alerts = _alerts()
    await _cycle(execution, alerts)
    await _cycle(execution, alerts)
    await _cycle(execution, alerts)
    rows = await _failure_rows(feed, book)
    assert len(rows) == 1
    assert rows[0].is_open


async def test_a_pass_that_warmed_the_window_cache_proves_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warming is unknown work, even when the stopped book's pass returned."""
    original = CycleTimer._keep_book

    def warming(self: CycleTimer, deployment: Deployment, probe: BookProbe, *args: Any) -> None:
        original(self, deployment, probe, *args)
        probe.warming = True

    monkeypatch.setattr(CycleTimer, "_keep_book", warming)
    monkeypatch.setattr(worker_service, "_process_one", _passes(RuntimeError("boom"), True))
    execution = InMemoryExecutionStore()
    book = _book()
    await execution.create_deployment(book)
    feed, alerts = _alerts()
    await _cycle(execution, alerts)
    await _cycle(execution, alerts)
    (row,) = await _failure_rows(feed, book)
    assert row.is_open


async def test_a_failure_in_the_same_cycle_wins_over_the_maintenance_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stopped book that fails again stays open and keeps counting."""
    monkeypatch.setattr(
        worker_service, "_process_one", _passes(RuntimeError("one"), RuntimeError("two"))
    )
    execution = InMemoryExecutionStore()
    book = _book()
    await execution.create_deployment(book)
    feed, alerts = _alerts()
    await _cycle(execution, alerts)
    await _cycle(execution, alerts)
    (row,) = await _failure_rows(feed, book)
    assert row.is_open
    assert row.occurrences == 2


async def _process(execution: InMemoryExecutionStore, book: Deployment) -> bool:
    return await _process_one(
        deployment_id=book.id,
        store=execution,
        publication_store=InMemoryStrategyStore(),
        market_data=MarketDataService(DemoMarketData()),
        paper_broker=PaperBroker(),
        live_broker=None,
        quote_reader=None,
        risk_policy=(await load_effective_policy(None)).definition,
        portfolio=(),
        user_feed_store=None,
        memory_store=None,
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (DeploymentStatus.STOPPED, True),
        (DeploymentStatus.RUNNING, False),
        (DeploymentStatus.PAUSED, False),
    ],
)
async def test_process_one_reports_only_a_completed_stopped_maintenance_pass(
    status: DeploymentStatus, expected: bool
) -> None:
    """The real worker step returns true for a stopped book it maintained, and only then."""
    execution = InMemoryExecutionStore()
    book = _book(status=status)
    await execution.create_deployment(book)
    assert await _process(execution, book) is expected


async def test_a_lease_skipped_stopped_book_is_not_reported_maintained() -> None:
    """Another worker holding the lease means this pass did no work."""
    execution = InMemoryExecutionStore()
    book = _book()
    await execution.create_deployment(book)
    leased = await acquire_worker_lease(execution, book.id, holder="another-worker")
    assert leased is not None
    assert await _process(execution, book) is False
