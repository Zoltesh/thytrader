"""Supervision writes respect leases, revision fences, and manual lifecycle choices."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest

from tests.alerts.test_alert_service import _BlockingSender
from tests.alerts.test_supervision import _candle, _position
from tests.execution_worker.test_safety_supervision import _NOW, _cycle, _deployment
from thytrader.alerts.models import AlertCode
from thytrader.alerts.service import AlertService
from thytrader.alerts.store import AlertStoreError, InMemoryAlertStore
from thytrader.alerts.supervision import AlertThresholds, worker_book_failure_finding
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import DeploymentStatus, LifecycleCommand, OrderSide, RuntimePhase
from thytrader.execution_worker import service as worker_service
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import parse_candle_interval
from thytrader.market_data.service import MarketDataService
from thytrader.memory.notify import DisabledNotificationSender

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.alerts.models import OperatorAlert
    from thytrader.execution.models import Deployment
    from thytrader.market_data.models import Candle, MarketProduct

pytestmark = pytest.mark.anyio
Race = Literal["stop", "pause", "delete", "revision", "strategy", "mismatch", "lease"]


def _service(store: InMemoryAlertStore) -> AlertService:
    """Recreate the service while retaining the same durable-store evidence."""
    return AlertService(store, DisabledNotificationSender(), thresholds=AlertThresholds())


def _race(store: InMemoryExecutionStore, book: Deployment, mutation: Race) -> None:
    """Simulate a concurrent operator/worker transaction in the in-memory store."""
    current = store.deployments[book.id]
    if mutation == "delete":
        del store.deployments[book.id]
    elif mutation == "stop":
        store.deployments[book.id] = replace(
            current,
            revision=current.revision + 1,
            status=DeploymentStatus.STOPPED,
            lifecycle_command=LifecycleCommand.MANAGED_SHUTDOWN,
        )
    elif mutation == "pause":
        store.deployments[book.id] = replace(
            current,
            revision=current.revision + 1,
            status=DeploymentStatus.PAUSED,
            lifecycle_command=LifecycleCommand.STOP_NEW_ENTRIES,
            mismatch_detail="operator pause",
        )
    elif mutation == "strategy":
        store.deployments[book.id] = replace(
            current, revision=current.revision + 1, strategy_id=uuid4()
        )
    elif mutation == "mismatch":
        store.deployments[book.id] = replace(
            current, revision=current.revision + 1, mismatch_detail="different mismatch"
        )
    elif mutation == "lease":
        store.deployments[book.id] = replace(
            current,
            revision=current.revision + 1,
            worker_lease_holder="other-worker",
            worker_lease_expires_at=worker_service.utc_now() + timedelta(minutes=5),
        )
    else:
        store.deployments[book.id] = replace(current, revision=current.revision + 1)


@pytest.mark.parametrize(
    "mutation", ["stop", "pause", "delete", "revision", "strategy", "mismatch", "lease"]
)
@pytest.mark.parametrize("stage", ["before_lease", "before_save"])
async def test_pause_races_never_overwrite_concurrent_book_state(
    monkeypatch: pytest.MonkeyPatch, mutation: Race, stage: str
) -> None:
    """Any mutation before lease/read/save wins; error evidence remains durable and open."""
    execution = InMemoryExecutionStore()
    book = await execution.create_deployment(_deployment())
    alerts = InMemoryAlertStore()
    application = None
    for index in range(3):
        application = await _service(alerts).apply(
            (worker_book_failure_finding(book, error_type="RuntimeError"),),
            now=_NOW + timedelta(seconds=index),
        )
    assert application is not None
    original_acquire = execution.acquire_worker_lease
    original_save = execution.save_deployment

    async def acquire(
        deployment_id: UUID, *, holder: str, now: datetime, ttl: timedelta
    ) -> Deployment | None:
        """Inject a race immediately before the actual lease update."""
        if stage == "before_lease":
            _race(execution, book, mutation)
        return await original_acquire(deployment_id, holder=holder, now=now, ttl=ttl)

    async def save(deployment: Deployment, *, expected_revision: int | None = None) -> Deployment:
        """Inject a race after re-read, requiring the write's revision fence to stop it."""
        if stage == "before_save":
            _race(execution, book, mutation)
        return await original_save(deployment, expected_revision=expected_revision)

    monkeypatch.setattr(execution, "acquire_worker_lease", acquire)
    monkeypatch.setattr(execution, "save_deployment", save)
    await worker_service._pause_repeatedly_failing_books(
        execution,
        application,
        deployments=(book,),
        consecutive_failure_cycles=3,
    )
    if mutation == "delete":
        assert book.id not in execution.deployments
    else:
        current = execution.deployments[book.id]
        assert not (current.mismatch_detail or "").startswith("WORKER_CONSECUTIVE_FAILURES")
        if mutation == "stop":
            assert current.status is DeploymentStatus.STOPPED
        if mutation == "pause":
            assert current.status is DeploymentStatus.PAUSED
            assert current.mismatch_detail == "operator pause"
    assert (await alerts.list_open_alerts())[0].occurrences == 3


async def test_restart_failure_count_and_pause_evidence_survive_unknown_cycles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restart neither resets accumulated errors nor clears the persisted supervision pause."""
    execution = InMemoryExecutionStore()
    book = await execution.create_deployment(_deployment())
    alerts = InMemoryAlertStore()
    calls = 0

    async def fail(**kwargs: object) -> None:
        """Fail actual attempted work, not a lease skip."""
        nonlocal calls
        del kwargs
        calls += 1
        raise RuntimeError("failed evaluation")

    async def unknown(**kwargs: object) -> None:
        """A non-raising lease skip/warming/no-decision pass proves no error recovery."""
        nonlocal calls
        del kwargs
        calls += 1

    monkeypatch.setattr(worker_service, "_process_one", fail)
    await _cycle(execution, _service(alerts))
    await _cycle(execution, _service(alerts))
    monkeypatch.setattr(worker_service, "_process_one", unknown)
    await _cycle(execution, _service(alerts))
    failure = next(
        row for row in await alerts.list_open_alerts() if row.code is AlertCode.WORKER_BOOK_FAILURES
    )
    assert failure.occurrences == 2
    monkeypatch.setattr(worker_service, "_process_one", fail)
    await _cycle(execution, _service(alerts))
    assert execution.deployments[book.id].status is DeploymentStatus.PAUSED
    monkeypatch.setattr(worker_service, "_process_one", unknown)
    await _cycle(execution, _service(alerts))
    assert calls == 5
    failure = next(
        row for row in await alerts.list_open_alerts() if row.code is AlertCode.WORKER_BOOK_FAILURES
    )
    assert failure.occurrences == 3  # Observing the persisted latch is not another error.
    assert execution.deployments[book.id].lifecycle_command is LifecycleCommand.STOP_NEW_ENTRIES


async def test_persisted_threshold_before_pause_crash_is_applied_on_restart_without_new_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash between durable threshold and fenced pause cannot reset or bypass the pause."""
    execution = InMemoryExecutionStore()
    book = await execution.create_deployment(_deployment())
    alerts = InMemoryAlertStore()
    for index in range(3):
        await _service(alerts).apply(
            (worker_book_failure_finding(book, error_type="RuntimeError"),),
            now=_NOW + timedelta(seconds=index),
            dispatch=False,
        )

    async def unknown(**kwargs: object) -> None:
        """A warming/lease-skip pass neither fails nor certifies recovery."""
        del kwargs

    monkeypatch.setattr(worker_service, "_process_one", unknown)
    await _cycle(execution, _service(alerts))
    assert execution.deployments[book.id].status is DeploymentStatus.PAUSED
    assert (
        next(
            row
            for row in await alerts.list_open_alerts()
            if row.code is AlertCode.WORKER_BOOK_FAILURES
        ).occurrences
        == 3
    )


async def test_slow_notifications_never_run_inline_with_safety_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observation/pause path persists locally without invoking the optional network sender."""
    execution = InMemoryExecutionStore()
    book = await execution.create_deployment(_deployment())
    alerts = InMemoryAlertStore()
    sender = _BlockingSender()
    service = AlertService(alerts, sender, thresholds=AlertThresholds())

    async def fail(**kwargs: object) -> None:
        """Failure must persist even though sender would block waiting for a callback."""
        del kwargs
        raise RuntimeError("decision failure")

    monkeypatch.setattr(worker_service, "_process_one", fail)
    await _cycle(execution, service)
    assert not sender.started.is_set()
    assert any(row.deployment_id == book.id for row in await alerts.list_open_alerts())


@pytest.mark.parametrize("failure", ["inventory", "alerts"])
async def test_inventory_and_alert_reads_failing_never_recover_prior_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Storage/list failures skip the pass without manufacturing an empty healthy inventory."""
    execution = InMemoryExecutionStore()
    book = await execution.create_deployment(_deployment())
    alerts = InMemoryAlertStore()
    await _service(alerts).apply(
        (worker_book_failure_finding(book, error_type="RuntimeError"),), now=_NOW
    )

    async def unavailable_books(
        *, limit: int | None = None, offset: int = 0
    ) -> tuple[Deployment, ...]:
        """A failed authoritative inventory call is not a confirmed deletion."""
        del limit, offset
        raise RuntimeError("inventory unavailable")

    async def unavailable_alerts() -> tuple[OperatorAlert, ...]:
        """A failed durable alert query cannot return a fabricated empty set."""
        raise AlertStoreError("alert inventory unavailable")

    if failure == "inventory":
        monkeypatch.setattr(execution, "list_deployments", unavailable_books)
    else:
        monkeypatch.setattr(alerts, "list_open_alerts", unavailable_alerts)
    await worker_service._supervise_safety(
        alert_service=_service(alerts),
        store=execution,
        market_data=_test_market_data(),
        deployments=(book,),
        cycle_failures=[],
        worker_interval_seconds=30,
        observed_at=_NOW + timedelta(seconds=1),
    )
    assert next(iter(alerts.history.values()))[0].resolved_at is None
    assert execution.deployments[book.id].status is DeploymentStatus.RUNNING


def _test_market_data() -> MarketDataService:
    """Provide a hermetic adapter for read-failure tests that should not reach it."""
    return MarketDataService(DemoMarketData())


async def test_real_restarted_paper_book_remains_paused_and_executes_risk_reducing_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real process-one path maintains/exits a persisted pause, without opening entries."""
    execution = InMemoryExecutionStore()
    book = await execution.create_deployment(
        replace(
            _deployment(
                status=DeploymentStatus.PAUSED,
                lifecycle_command=LifecycleCommand.STOP_NEW_ENTRIES,
                mismatch_detail="WORKER_CONSECUTIVE_FAILURES: operator review required",
            ),
            phase=RuntimePhase.OPEN,
            last_evaluated_bar=_NOW - timedelta(hours=1),
            paper_maker_fee_rate=Decimal("0.001"),
            paper_taker_fee_rate=Decimal("0.002"),
        )
    )
    await execution.save_position(
        replace(_position(book), quantity=Decimal("1")),
        deployment_id=book.id,
        product_id=book.product_id,
    )
    market_data = _test_market_data()
    preview = await market_data.get_preview(book.product_id, parse_candle_interval("1h"))
    candles = (
        _candle(
            start=_NOW - timedelta(hours=1),
            low=Decimal("99"),
            high=Decimal("101"),
            close=Decimal("100"),
        ),
        _candle(start=_NOW, low=Decimal("89"), high=Decimal("101"), close=Decimal("91")),
    )

    async def window(
        data: MarketDataService,
        *,
        product_id: str,
        timeframe: str,
        warmup_bars: int,
        deploy_anchor: datetime,
        as_of_closed_start: datetime | None = None,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        """Return explicit paper-test bars; no actual exchange requests are possible."""
        del data, product_id, timeframe, warmup_bars, deploy_anchor, as_of_closed_start
        return preview.product, candles, candles[-1].starts_at

    monkeypatch.setattr(worker_service, "_closed_window_for", window)
    alerts = InMemoryAlertStore()
    await _cycle(execution, _service(alerts))
    snapshot = await execution.get_deployment(book.id)
    assert snapshot.deployment.status is DeploymentStatus.PAUSED
    assert snapshot.deployment.lifecycle_command is LifecycleCommand.STOP_NEW_ENTRIES
    assert snapshot.deployment.mismatch_detail == book.mismatch_detail
    assert snapshot.position is None
    assert snapshot.fills  # A real paper risk-reducing execution, not a no-op stub.
    assert all(order.side is OrderSide.SELL for order in snapshot.orders)
