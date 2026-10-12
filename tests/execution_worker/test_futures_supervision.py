"""The worker's futures observer is read-only, isolated and restart-safe."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.alerts.test_alert_service import _service
from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_paper_futures_books import _policy
from tests.operator_diagnostics.test_futures_account_report import _Store
from tests.operator_diagnostics.test_futures_books_report import _AT, _snapshot
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    InMemoryAuditEventStore,
)
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.futures_live import FuturesLiveRuntime, live_futures_runtime_scope
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker import futures_supervision, service
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.risk.futures_collateral import risk_futures_account_scope
from thytrader.strategies.memory_store import InMemoryStrategyStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, PositionSide

pytestmark = pytest.mark.anyio


async def test_orphan_audit_is_a_single_restart_safe_observation() -> None:
    """Replaying an existing cancellation event does not cancel or notify twice."""
    audit = InMemoryAuditEventStore()
    event = AuditEvent(
        occurred_at=_AT,
        category=AuditEventCategory.RUNTIME,
        action="futures_orphan_order_cancelled",
        outcome=AuditEventOutcome.INFO,
        product_id="BIP-20DEC30-CDE",
        detail="Confirmed managed orphan cancellation.",
    )
    await audit.append(event)
    alerts = InMemoryAlertStore()
    for tick in range(2):
        with execution_audit_scope(audit):
            await futures_supervision.supervise_futures(
                alert_service=_service(alerts),
                store=InMemoryExecutionStore(),
                policy=_policy(),
                observed_at=_AT + timedelta(seconds=tick),
            )
    rows = await alerts.list_open_alerts()
    assert len(rows) == 1
    assert rows[0].code.value == "FUTURES_ORPHAN_ORDER"
    assert rows[0].subject == f"orphan:{event.id}"
    assert rows[0].occurrences == 1


async def test_worker_observer_raises_from_real_inventory_without_order_calls() -> None:
    """Use a live book and the real injected source composition, never an alert mock."""
    book = _snapshot(side=PositionSide.LONG, cash="0")
    book = replace(book, deployment=replace(book.deployment, mode=DeploymentMode.LIVE))
    store = InMemoryExecutionStore()
    await store.create_deployment(book.deployment)
    await store.save_position(book.position, deployment_id=book.deployment.id)
    alerts = InMemoryAlertStore()
    venue = FakeVenue()
    venue.now = _AT - timedelta(minutes=4)
    venue.net = Decimal(100)
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        result = await futures_supervision.supervise_futures(
            alert_service=_service(alerts), store=store, policy=_policy(), observed_at=_AT
        )
    assert result == "observed"
    assert any(
        row.code.value == "FUTURES_ACCOUNT_UNKNOWN" for row in await alerts.list_open_alerts()
    )
    assert venue.calls == []
    assert venue.account_reads == 1


async def test_cycle_runs_futures_observer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The actual cycle records a missing live account without running a trading step."""

    async def noop(**kwargs: object) -> None:
        """Isolate supervision from trading."""
        del kwargs

    monkeypatch.setattr(service, "_process_one", noop)
    book = _snapshot(side=PositionSide.LONG, cash="0")
    book = replace(book, deployment=replace(book.deployment, mode=DeploymentMode.LIVE))
    store = InMemoryExecutionStore()
    await store.create_deployment(book.deployment)
    await store.save_position(book.position, deployment_id=book.deployment.id)
    alerts = InMemoryAlertStore()
    await service._run_cycle(
        store=store,
        publication_store=InMemoryStrategyStore(),
        market_data=MarketDataService(DemoMarketData()),
        paper_broker=PaperBroker(),
        live_broker=None,
        quote_reader=None,
        risk_store=None,
        alert_service=_service(alerts),
    )
    assert any(
        row.code.value == "FUTURES_ACCOUNT_UNKNOWN" for row in await alerts.list_open_alerts()
    )


async def test_spot_only_observer_makes_no_cfm_reads() -> None:
    """No additional CFM GET occurs for an empty or spot-only inventory."""
    venue = FakeVenue()
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        result = await futures_supervision.supervise_futures(
            alert_service=_service(InMemoryAlertStore()),
            store=InMemoryExecutionStore(),
            policy=_policy(),
            observed_at=_AT,
        )
    assert result == "observed"
    assert venue.account_reads == 0


async def test_failed_mirror_raises_blindness_without_claiming_success() -> None:
    """An unreadable mirror cannot suppress the held-account unknown finding."""
    book = _snapshot(side=PositionSide.LONG, cash="0")
    book = replace(book, deployment=replace(book.deployment, mode=DeploymentMode.LIVE))
    store = InMemoryExecutionStore()
    await store.create_deployment(book.deployment)
    await store.save_position(book.position, deployment_id=book.deployment.id)
    alerts = InMemoryAlertStore()
    with risk_futures_account_scope(_Store(None, fail=True)):
        result = await futures_supervision.supervise_futures(
            alert_service=_service(alerts),
            store=store,
            policy=_policy(),
            observed_at=_AT,
        )
    assert result == "failed"
    assert any(
        row.code.value == "FUTURES_ACCOUNT_UNKNOWN" for row in await alerts.list_open_alerts()
    )


async def test_observer_failure_is_not_all_clear() -> None:
    """An inventory exception is isolated and explicitly reported as a failed observation."""

    class BrokenStore(InMemoryExecutionStore):
        """A failed complete inventory must never apply recovery checks."""

        async def list_deployments(self, *, limit: int | None = None, offset: int = 0) -> tuple[()]:
            """Fail before claiming coverage."""
            del limit, offset
            raise RuntimeError("synthetic unavailable")

    alerts = InMemoryAlertStore()
    result = await futures_supervision.supervise_futures(
        alert_service=_service(alerts),
        store=BrokenStore(),
        policy=_policy(),
        observed_at=_AT,
    )
    assert result == "failed"
    assert any(
        row.code.value == "FUTURES_ACCOUNT_UNKNOWN" for row in await alerts.list_open_alerts()
    )
