"""A worker failure on a stopped book resolves once its maintenance pass completes (ADR 0115).

A stopped book makes no decisions, so its decision cursor never advances and the decision rule
alone left such a failure open forever.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from tests.execution import test_adr_0110_stopped_lifecycle as lifecycle
from thytrader.alerts.models import AlertCode
from thytrader.alerts.service import AlertService
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.alerts.supervision_inputs import AlertThresholds
from thytrader.alerts.supervision_rows import (
    STOPPED_MAINTENANCE_RECOVERY,
    worker_book_failure_finding,
)
from thytrader.execution.leases import acquire_worker_lease
from thytrader.execution.paper import PaperBroker
from thytrader.execution.stopped import (
    STOPPED_BROKER_UNAVAILABLE,
    maintained_without_fault,
    supervise_stopped_deployment,
)
from thytrader.execution_worker import service as worker_service
from thytrader.execution_worker.cycle_timing import BookProbe, CycleTimer
from thytrader.execution_worker.service import _process_one, _run_cycle
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.memory.notify import DisabledNotificationSender
from thytrader.risk.store import load_effective_policy
from thytrader.strategies.memory_store import InMemoryStrategyStore
from thytrader.trading.ids import utc_now
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    LifecycleCommand,
    RuntimePhase,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from thytrader.alerts.models import OperatorAlert
    from thytrader.alerts.store import AlertStore
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.trading.store import ExecutionStore

pytestmark = pytest.mark.anyio
_NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
_PAUSE = "WORKER_CONSECUTIVE_FAILURES: entries paused after 3 failed supervision cycles."


def _book(
    *,
    status: DeploymentStatus = DeploymentStatus.STOPPED,
    mismatch: str | None = None,
    mode: DeploymentMode = DeploymentMode.PAPER,
) -> Deployment:
    """One flat discretionary book in ``status`` (paper unless ``mode`` says otherwise)."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint=None,
        strategy_id=None,
        product_id="BTC-USD",
        mode=mode,
        status=status,
        cash=Decimal("1000"),
        phase=RuntimePhase.FLAT,
        created_at=_NOW - timedelta(days=1),
        updated_at=_NOW,
        kind=DeploymentKind.DISCRETIONARY,
        timeframe="1h",
        mismatch_detail=mismatch,
        paper_starting_cash=Decimal("1000") if mode is DeploymentMode.PAPER else None,
        paper_maker_fee_rate=Decimal("0.001") if mode is DeploymentMode.PAPER else None,
        paper_taker_fee_rate=Decimal("0.002") if mode is DeploymentMode.PAPER else None,
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
        # Any fail-closed fault on the stopped book means its maintenance is not proven.
        (DeploymentStatus.STOPPED, "Order reconciliation is unconfirmed: order read failed."),
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


async def _seeded(
    book: Deployment,
) -> tuple[InMemoryExecutionStore, InMemoryAlertStore, AlertService]:
    """A store holding ``book`` and an open worker failure on it from an earlier cycle."""
    execution = InMemoryExecutionStore()
    await execution.create_deployment(book)
    feed, alerts = _alerts()
    await feed.record(
        worker_book_failure_finding(book, error_type="ExecutionConflictError"),
        now=utc_now() - timedelta(minutes=5),
    )
    return execution, feed, alerts


async def test_a_real_quiet_stopped_paper_pass_resolves_the_failure() -> None:
    """Through the real worker step: a flat, fault-free stopped book proves its recovery."""
    book = _book()
    execution, feed, alerts = await _seeded(book)
    await _cycle(execution, alerts)
    (row,) = await _failure_rows(feed, book)
    assert not row.is_open
    assert row.resolution_detail == STOPPED_MAINTENANCE_RECOVERY


async def test_a_stopped_live_book_without_a_broker_keeps_its_failure_open() -> None:
    """No live broker means no reconciliation: the pass returns but proves nothing."""
    book = _book(mode=DeploymentMode.LIVE)
    execution, feed, alerts = await _seeded(book)
    await _cycle(execution, alerts)
    await _cycle(execution, alerts)
    (row,) = await _failure_rows(feed, book)
    assert row.is_open
    noted = (await execution.get_deployment(book.id)).deployment
    assert noted.mismatch_detail == STOPPED_BROKER_UNAVAILABLE


@pytest.mark.parametrize(
    ("maintained", "change", "expected"),
    [
        (True, {}, True),
        (False, {}, False),
        (
            True,
            {"mismatch_detail": "Order reconciliation is unconfirmed: order read failed."},
            False,
        ),
        (True, {"status": DeploymentStatus.PAUSED}, False),
        (True, {"futures_derisk_pending": True}, False),
        (True, {"futures_flat_confirmation_after": _NOW}, False),
    ],
)
def test_maintained_without_fault_needs_the_outcome_and_a_clean_stopped_book(
    maintained: bool, change: dict[str, object], expected: bool
) -> None:
    """The pass's outcome alone is not enough: the book must be stopped and fault-free after it."""
    book = DeploymentSnapshot(deployment=_book(), orders=(), fills=(), position=None)
    book = replace(book, deployment=replace(book.deployment, **change))
    assert maintained_without_fault(book, maintained=maintained) is expected


def test_an_incompletely_accounted_book_is_not_maintained_without_fault() -> None:
    """Unknown accounting is not a clean stopped book."""
    book = DeploymentSnapshot(
        deployment=_book(), orders=(), fills=(), position=None, accounting_complete=False
    )
    assert not maintained_without_fault(book, maintained=True)


async def _stopped_outcome(
    *, command: LifecycleCommand, candles: bool, position: bool = True
) -> bool:
    """Run real stopped supervision on a paper discretionary book and return its outcome."""
    store = InMemoryExecutionStore()
    deployment = await lifecycle._deployment(
        store, mode=DeploymentMode.PAPER, kind=DeploymentKind.DISCRETIONARY, command=command
    )
    if position:
        await lifecycle._position(store, deployment.id, "BTC-USD")
    priced = {"BTC-USD": (lifecycle._candle(),)} if candles else {}

    async def window(
        market_data: object,
        *,
        product_id: str,
        timeframe: str,
        warmup_bars: int,
        deploy_anchor: datetime,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        return await lifecycle._windows(
            market_data,
            product_id=product_id,
            timeframe=timeframe,
            warmup_bars=warmup_bars,
            deploy_anchor=deploy_anchor,
            candles_by_product=priced,
        )

    return await supervise_stopped_deployment(
        await store.get_deployment(deployment.id),
        strategy=None,
        store=store,
        market_data=lifecycle._as_market_data(lifecycle._EmptyPreview()),
        paper_broker=lifecycle._RecordingPaper(),
        live_broker=None,
        load_closed_window=window,
    )


@pytest.mark.parametrize(
    ("command", "candles", "position", "expected"),
    [
        # Protection maintained at a verified close: verified work, inventory or not.
        (LifecycleCommand.MANAGED_SHUTDOWN, True, True, True),
        # A held position without its price context: nothing could be maintained.
        (LifecycleCommand.MANAGED_SHUTDOWN, False, True, False),
        # A deferred flatten keeps protection but did not do the flatten.
        (LifecycleCommand.FLATTEN, False, True, False),
        (LifecycleCommand.FLATTEN, True, True, True),
        # Nothing held: nothing to maintain.
        (LifecycleCommand.MANAGED_SHUTDOWN, False, False, True),
    ],
)
async def test_stopped_supervision_reports_whether_it_carried_out_the_maintenance(
    command: LifecycleCommand, candles: bool, position: bool, expected: bool
) -> None:
    """The outcome is positive evidence from the real maintenance paths."""
    assert await _stopped_outcome(command=command, candles=candles, position=position) is expected


async def _no_window(
    market_data: object,
    *,
    product_id: str,
    timeframe: str,
    warmup_bars: int,
    deploy_anchor: datetime,
) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
    """No verified candles for any product."""
    return await lifecycle._windows(
        market_data,
        product_id=product_id,
        timeframe=timeframe,
        warmup_bars=warmup_bars,
        deploy_anchor=deploy_anchor,
        candles_by_product={},
    )


async def test_a_returning_broker_clears_the_broker_unavailable_note() -> None:
    """The note outlived the outage forever; a broker that reconciles cleanly clears it."""
    store = InMemoryExecutionStore()
    book = _book(mode=DeploymentMode.LIVE, mismatch=STOPPED_BROKER_UNAVAILABLE)
    await store.create_deployment(book)
    carried = await supervise_stopped_deployment(
        await store.get_deployment(book.id),
        strategy=None,
        store=store,
        market_data=MarketDataService(DemoMarketData()),
        paper_broker=PaperBroker(),
        live_broker=PaperBroker(),
        load_closed_window=_no_window,
    )
    after = (await store.get_deployment(book.id)).deployment
    assert carried
    assert after.mismatch_detail is None
    assert after.status is DeploymentStatus.STOPPED


async def assert_stopped_maintenance_resolves_with_its_note(
    alerts_store: AlertStore, execution: ExecutionStore | None = None
) -> None:
    """The second apply at the cycle watermark resolves a maintained stopped book's failure."""
    book = _book()
    execution = execution or InMemoryExecutionStore()
    await execution.create_deployment(book)
    await alerts_store.record(
        worker_book_failure_finding(book, error_type="ExecutionConflictError"),
        now=_NOW - timedelta(minutes=5),
    )
    await worker_service._supervise_safety(
        alert_service=_service(alerts_store),
        store=execution,
        market_data=MarketDataService(DemoMarketData()),
        deployments=(book,),
        cycle_failures=[],
        worker_interval_seconds=30,
        observed_at=_NOW,
        stopped_maintained=frozenset({book.id}),
    )
    (row,) = await alerts_store.list_resolved(
        AlertCode.WORKER_BOOK_FAILURES.value, str(book.id), limit=5
    )
    assert row.resolution_detail == STOPPED_MAINTENANCE_RECOVERY


async def assert_a_same_cycle_failure_wins_the_maintenance_tie(
    alerts_store: AlertStore, execution: ExecutionStore | None = None
) -> None:
    """A failure recorded at the same watermark keeps the row open, whatever the store."""
    book = _book()
    execution = execution or InMemoryExecutionStore()
    await execution.create_deployment(book)
    failure = worker_book_failure_finding(book, error_type="ReadTimeout")
    await worker_service._supervise_safety(
        alert_service=_service(alerts_store),
        store=execution,
        market_data=MarketDataService(DemoMarketData()),
        deployments=(book,),
        cycle_failures=[failure],
        worker_interval_seconds=30,
        observed_at=_NOW,
        stopped_maintained=frozenset({book.id}),
    )
    rows = [row for row in await alerts_store.list_open_alerts() if row.subject == str(book.id)]
    assert len(rows) == 1


def _service(store: AlertStore) -> AlertService:
    return AlertService(
        store,
        DisabledNotificationSender(),
        thresholds=AlertThresholds(consecutive_failure_cycles=3, delivery_max_attempts=2),
    )


async def test_stopped_maintenance_resolves_with_its_note() -> None:
    """In memory; ``test_postgres_alerts`` runs the same scenario against PostgreSQL."""
    await assert_stopped_maintenance_resolves_with_its_note(InMemoryAlertStore())


async def test_a_same_cycle_failure_wins_the_maintenance_tie() -> None:
    """In memory; ``test_postgres_alerts`` runs the same scenario against PostgreSQL."""
    await assert_a_same_cycle_failure_wins_the_maintenance_tie(InMemoryAlertStore())
