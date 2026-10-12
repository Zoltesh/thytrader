"""Required observation failures stay durable across stopped books and restart."""

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from tests.alerts.test_alert_service import _service
from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_paper_futures_books import _policy
from tests.operator_diagnostics.test_futures_account_report import _Store
from tests.operator_diagnostics.test_futures_books_report import _AT, _snapshot
from thytrader.alerts.models import AlertCode
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.execution.futures_live import FuturesLiveRuntime, live_futures_runtime_scope
from thytrader.execution_worker.futures_supervision import supervise_futures
from thytrader.operator.futures_live_health import futures_live_health_component
from thytrader.risk.futures_collateral import risk_futures_account_scope
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentKind, DeploymentMode, DeploymentStatus, PositionSide

if TYPE_CHECKING:
    from thytrader.alerts.store import AlertStore
    from thytrader.trading.store import ExecutionStore

pytestmark = pytest.mark.anyio


async def test_observer_requires_full_accounting_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    """A display-bounded book read cannot stand in for full signed-ledger recovery."""
    book = _snapshot(side=PositionSide.LONG, cash="0")
    store = InMemoryExecutionStore()
    await store.create_deployment(replace(book.deployment, mode=DeploymentMode.LIVE))
    read = AsyncMock(wraps=store.get_accounting_snapshot)
    monkeypatch.setattr(store, "get_accounting_snapshot", read)
    await supervise_futures(
        alert_service=_service(InMemoryAlertStore()), store=store, policy=_policy(), observed_at=_AT
    )
    read.assert_awaited_once_with(book.deployment.id)


async def test_partial_stopped_read_keeps_observer_health_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incomplete accounting cannot disappear when managed-scope filtering sees local flat."""
    book = _snapshot(side=PositionSide.LONG, cash="0")
    book = replace(
        book,
        position=None,
        accounting_complete=False,
        deployment=replace(
            book.deployment, mode=DeploymentMode.LIVE, status=DeploymentStatus.STOPPED
        ),
    )
    store = InMemoryExecutionStore()
    await store.create_deployment(book.deployment)
    monkeypatch.setattr(store, "get_accounting_snapshot", AsyncMock(return_value=book))
    alerts = InMemoryAlertStore()
    result = await supervise_futures(
        alert_service=_service(alerts), store=store, policy=_policy(), observed_at=_AT
    )
    assert result == "failed"
    assert any(a.code is AlertCode.FUTURES_ACCOUNT_UNKNOWN for a in await alerts.list_open_alerts())


@pytest.mark.parametrize("duty", ["flat", "derisk", "none"])
async def test_stopped_observer_failure_is_durable_and_flat_proof_recovers(duty: str) -> None:
    """Stopped historical flat books need no read; outstanding duties do, even at age zero."""
    await stopped_failure_scenario(InMemoryExecutionStore(), InMemoryAlertStore(), duty)


async def stopped_failure_scenario(store: ExecutionStore, alerts: AlertStore, duty: str) -> None:
    """Persist and recover the actual observer's failed observation in either store."""
    book = _snapshot(side=PositionSide.LONG, cash="0")
    deployment = replace(
        book.deployment,
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.STOPPED,
        kind=DeploymentKind.DISCRETIONARY,
        strategy_fingerprint=None,
        strategy_id=None,
        paper_starting_cash=None,
        paper_maker_fee_rate=None,
        paper_taker_fee_rate=None,
        futures_flat_confirmation_after=_AT if duty == "flat" else None,
        futures_derisk_pending=duty == "derisk",
    )
    await store.create_deployment(deployment)
    with risk_futures_account_scope(_Store(None, fail=True)):
        outcome = await supervise_futures(
            alert_service=_service(alerts), store=store, policy=_policy(), observed_at=_AT
        )
    if duty == "none":
        assert outcome == "observed"
        assert not await alerts.list_open_alerts()
        return
    assert outcome == "failed"
    assert any(a.code is AlertCode.FUTURES_ACCOUNT_UNKNOWN for a in await alerts.list_open_alerts())
    assert (await futures_live_health_component(alerts)).status.value != "healthy"
    with risk_futures_account_scope(_Store(None)):
        await supervise_futures(
            alert_service=_service(alerts),
            store=store,
            policy=_policy(),
            observed_at=_AT + timedelta(seconds=1),
        )
    assert any(a.code is AlertCode.FUTURES_ACCOUNT_UNKNOWN for a in await alerts.list_open_alerts())
    venue = FakeVenue()
    for seconds in (1, 2):
        venue.now = _AT + timedelta(seconds=seconds)
        with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
            await supervise_futures(
                alert_service=_service(alerts), store=store, policy=_policy(), observed_at=venue.now
            )
        unknown = any(
            a.code is AlertCode.FUTURES_ACCOUNT_UNKNOWN for a in await alerts.list_open_alerts()
        )
        assert not unknown
    assert venue.calls == []
