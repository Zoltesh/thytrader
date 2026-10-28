"""The worker's desk pass raises and resolves desk wake alerts and never fails a cycle."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from thytrader.agent_desk.models import Desk, DeskAction, DeskWake, WakeOutcome
from thytrader.agent_desk.store import DeskStoreUnavailableError, InMemoryDeskStore
from thytrader.alerts.models import AlertCode, AlertSeverity
from thytrader.alerts.service import AlertService
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.alerts.supervision_inputs import AlertThresholds
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker import desk_supervision
from thytrader.execution_worker.desk_supervision import supervise_desks
from thytrader.execution_worker.service import run_execution_worker
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.memory.notify import DisabledNotificationSender
from thytrader.strategies.memory_store import InMemoryStrategyStore
from thytrader.trading.memory import InMemoryExecutionStore

pytestmark = pytest.mark.anyio
_START = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


async def _live_desk(store: InMemoryDeskStore) -> Desk:
    """A live 30-minute desk."""
    return await store.create_desk(
        Desk(
            desk_id="desk-live",
            name="live desk",
            mode="live",
            live_acknowledged_at=_START,
            quote_currency="USDC",
            starting_cash=Decimal(150),
            cash=Decimal(150),
            products=("BTC-USDC",),
            created_at=_START - timedelta(days=1),
            updated_at=_START - timedelta(days=1),
        )
    )


async def _add(store: InMemoryDeskStore, outcome: WakeOutcome, at: datetime) -> None:
    """Journal one wake."""
    await store.add_wake(
        DeskWake(
            wake_id=f"w-{at.isoformat()}",
            desk_id="desk-live",
            started_at=at,
            finished_at=at,
            harness="pi",
            briefing_fingerprint="sha256:" + "a" * 64,
            raw_response="",
            action=DeskAction.NO_TRADE if outcome is WakeOutcome.APPLIED else DeskAction.INVALID,
            outcome=outcome,
        )
    )


async def test_a_failing_live_desk_pages_critical_and_resolves_once_it_answers() -> None:
    """Three failed wakes open a critical alert; the next answered wake resolves it."""
    desks = InMemoryDeskStore()
    alerts = AlertService(
        InMemoryAlertStore(), DisabledNotificationSender(), thresholds=AlertThresholds()
    )
    await _live_desk(desks)
    for minutes in (0, 30, 60):
        await _add(desks, WakeOutcome.ERROR, _START + timedelta(minutes=minutes))
    now = _START + timedelta(minutes=61)
    await supervise_desks(alert_service=alerts, desks=desks, clock=lambda: now)
    opened = await alerts.open_alerts()
    assert [(row.code, row.severity) for row in opened] == [
        (AlertCode.DESK_WAKES_FAILING, AlertSeverity.CRITICAL)
    ]
    await _add(desks, WakeOutcome.APPLIED, now + timedelta(minutes=29))
    later = now + timedelta(minutes=30)
    await supervise_desks(alert_service=alerts, desks=desks, clock=lambda: later)
    assert await alerts.open_alerts() == ()


class _BrokenDesks(InMemoryDeskStore):
    """A desk store whose reads fail."""

    async def list_desks(self) -> tuple[Desk, ...]:
        """Fail the desk listing."""
        raise DeskStoreUnavailableError("down")


async def test_an_unreadable_desk_store_never_fails_the_cycle() -> None:
    """The pass logs and returns; no alert is raised or resolved."""
    alerts = AlertService(
        InMemoryAlertStore(), DisabledNotificationSender(), thresholds=AlertThresholds()
    )
    await supervise_desks(alert_service=alerts, desks=_BrokenDesks(), clock=lambda: _START)
    await supervise_desks(alert_service=alerts, desks=None)
    assert await alerts.open_alerts() == ()


class _StoppingDesks(InMemoryDeskStore):
    """Stops the worker once the desk pass has listed the desks."""

    def __init__(self, stop: asyncio.Event) -> None:
        """Remember the worker's stop event."""
        super().__init__()
        self._stop = stop

    async def list_desks(self) -> tuple[Desk, ...]:
        """List, then ask the worker to stop after this cycle."""
        self._stop.set()
        return await super().list_desks()


async def test_the_worker_loop_runs_the_desk_pass_every_cycle() -> None:
    """A desk store handed to the worker is supervised by its cycle."""
    stop = asyncio.Event()
    desks = _StoppingDesks(stop)
    await _live_desk(desks)
    alerts = AlertService(
        InMemoryAlertStore(), DisabledNotificationSender(), thresholds=AlertThresholds()
    )
    await asyncio.wait_for(
        run_execution_worker(
            stop,
            store=InMemoryExecutionStore(),
            publication_store=InMemoryStrategyStore(),
            market_data=MarketDataService(DemoMarketData()),
            paper_broker=PaperBroker(),
            live_broker=None,
            quote_reader=None,
            interval_seconds=1,
            alert_service=alerts,
            desk_store=desks,
        ),
        timeout=30,
    )
    opened = await alerts.open_alerts()
    assert [(row.code, row.severity) for row in opened] == [
        (AlertCode.DESK_WAKES_STALLED, AlertSeverity.CRITICAL)
    ]


class _HangingDesks(InMemoryDeskStore):
    """A desk store whose listing never returns (a locked table)."""

    async def list_desks(self) -> tuple[Desk, ...]:
        """Block forever."""
        await asyncio.Event().wait()
        return ()


async def test_a_hanging_desk_store_is_abandoned_within_the_pass_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pass gives up after its budget; nothing is raised or resolved."""
    monkeypatch.setattr(desk_supervision, "PASS_SECONDS", 0.05)
    alerts = AlertService(
        InMemoryAlertStore(), DisabledNotificationSender(), thresholds=AlertThresholds()
    )
    await asyncio.wait_for(
        supervise_desks(alert_service=alerts, desks=_HangingDesks(), clock=lambda: _START),
        timeout=5,
    )
    assert await alerts.open_alerts() == ()
