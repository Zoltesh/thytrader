"""Worker-cycle supervision is decoupled from a successful signal evaluation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from tests.worker_patching import patch_worker_global
from thytrader.alerts.models import AlertCode, SafetyEvidence
from thytrader.alerts.service import AlertService
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.alerts.supervision import AlertThresholds
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    LifecycleCommand,
    RuntimePhase,
)
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker import service as worker_service
from thytrader.execution_worker.service import _run_cycle
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.memory.notify import DisabledNotificationSender
from thytrader.strategies.memory_store import InMemoryStrategyStore

pytestmark = pytest.mark.anyio
_NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)


def _deployment(
    *,
    status: DeploymentStatus = DeploymentStatus.RUNNING,
    lifecycle_command: LifecycleCommand = LifecycleCommand.NONE,
    mismatch_detail: str | None = None,
) -> Deployment:
    """One running flat paper book unless overridden."""
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
        worker_lease_expires_at=_NOW + timedelta(minutes=5),
        lifecycle_command=lifecycle_command,
        mismatch_detail=mismatch_detail,
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


async def test_supervision_freshness_uses_evaluation_time_not_cycle_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New receipts from this cycle are not future-dated against its older watermark."""
    evaluated_at = _NOW + timedelta(seconds=30)
    observed: list[datetime] = []

    async def gather(*, now: datetime, **kwargs: object) -> SafetyEvidence:
        del kwargs
        observed.append(now)
        return SafetyEvidence()

    patch_worker_global(monkeypatch, "utc_now", lambda: evaluated_at)
    patch_worker_global(monkeypatch, "gather_safety_findings", gather)
    _feed, alerts = _alerts()
    await worker_service._supervise_safety(
        alert_service=alerts,
        store=InMemoryExecutionStore(),
        market_data=MarketDataService(DemoMarketData()),
        deployments=(),
        cycle_failures=[],
        worker_interval_seconds=30,
        observed_at=_NOW,
    )
    assert observed == [evaluated_at]


async def test_supervision_records_a_mismatch_even_when_signal_evaluation_is_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Supervision findings do not depend on a successful signal evaluation pass."""
    calls = 0

    async def _noop(**kwargs: object) -> None:
        nonlocal calls
        del kwargs
        calls += 1

    monkeypatch.setattr(worker_service, "_process_one", _noop)
    execution = InMemoryExecutionStore()
    book = _deployment(status=DeploymentStatus.PAUSED, mismatch_detail="operator mismatch")
    await execution.create_deployment(book)
    feed, alerts = _alerts()
    await _cycle(execution, alerts)
    await _cycle(execution, alerts)
    rows = await feed.list_alerts(limit=10)
    assert len(rows) == 1
    assert rows[0].code is AlertCode.BOOK_PAUSED_MISMATCH
    assert rows[0].occurrences == 2
    assert calls == 2


async def test_consecutive_failures_pause_entries_and_keep_processing_the_book(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crossing the failure threshold pauses entries while the book keeps processing."""
    seen: list[object] = []

    async def _boom(**kwargs: object) -> None:
        seen.append(kwargs.get("deployment_id"))
        raise RuntimeError("signal evaluator crashed")

    monkeypatch.setattr(worker_service, "_process_one", _boom)
    execution = InMemoryExecutionStore()
    book = _deployment()
    await execution.create_deployment(book)
    _feed, alerts = _alerts()
    for _ in range(3):
        await _cycle(execution, alerts)
    paused = (await execution.get_deployment(book.id)).deployment
    assert paused.status is DeploymentStatus.PAUSED
    assert paused.mismatch_detail is not None
    assert paused.mismatch_detail.startswith("WORKER_CONSECUTIVE_FAILURES")
    await _cycle(execution, alerts)
    assert len(seen) == 4
    still = (await execution.get_deployment(book.id)).deployment
    assert still.status is DeploymentStatus.PAUSED


async def test_supervision_does_not_resume_a_user_pause_or_overwrite_its_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator pause with its own mismatch is never resumed or overwritten."""

    async def _boom(**kwargs: object) -> None:
        del kwargs
        raise RuntimeError("still failing")

    monkeypatch.setattr(worker_service, "_process_one", _boom)
    execution = InMemoryExecutionStore()
    book = _deployment(
        status=DeploymentStatus.PAUSED,
        lifecycle_command=LifecycleCommand.STOP_NEW_ENTRIES,
        mismatch_detail="operator paused entries",
    )
    await execution.create_deployment(book)
    _feed, alerts = _alerts()
    for _ in range(4):
        await _cycle(execution, alerts)
    kept = (await execution.get_deployment(book.id)).deployment
    assert kept.status is DeploymentStatus.PAUSED
    assert kept.lifecycle_command is LifecycleCommand.STOP_NEW_ENTRIES
    assert kept.mismatch_detail == "operator paused entries"
