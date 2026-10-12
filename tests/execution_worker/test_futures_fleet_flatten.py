"""Fleet command receipts reach the real candle-free futures worker, not just lifecycle flags."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.execution.decision_support import Catalog
from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from tests.execution.test_paper_futures_books import _binding, _Observations, _strategy
from tests.fleet_control.test_inventory_and_controls import _request
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.futures_live import FuturesLiveRuntime, live_futures_runtime_scope
from thytrader.execution.futures_paper import FuturesRuntime, futures_runtime_scope
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker.service import _process_one
from thytrader.fleet_control.models import (
    ExpectedTarget,
    FleetAction,
    FleetModeScope,
    FleetOperationStatus,
)
from thytrader.fleet_control.service import execute_fleet, preview_fleet
from thytrader.fleet_control.store import InMemoryFleetControlStore
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.trading.entry_latch import clear_entry_inhibition_cache
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import IntentPurpose, LifecycleCommand


@pytest.mark.anyio
@pytest.mark.parametrize("action", [FleetAction.FLATTEN, FleetAction.MANAGED_STOP])
async def test_fleet_commands_reduce_under_disarm_without_candles(
    monkeypatch: pytest.MonkeyPatch, action: FleetAction
) -> None:
    """Acknowledged fleet flatten and managed-stop margin supervision bypass entry gates."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    fleet = InMemoryFleetControlStore()
    store.bind_entry_gate(fleet)
    audit = InMemoryAuditEventStore()
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(snapshot))
    preview = await preview_fleet(
        execution=store, fleet=fleet, action=action, mode=FleetModeScope.LIVE
    )
    operation = await execute_fleet(
        execution=store,
        fleet=fleet,
        request=_request(
            action,
            mode=FleetModeScope.LIVE,
            live=True,
            targets=tuple(
                ExpectedTarget(item.deployment_id, item.revision) for item in preview.targets
            ),
        ),
        audit=audit,
    )
    assert operation.status is FleetOperationStatus.ACCEPTED
    commanded = await store.get_deployment(snapshot.deployment.id)
    assert commanded.position is not None  # A receipt is not a fill.
    expected_command = (
        LifecycleCommand.FLATTEN
        if action is FleetAction.FLATTEN
        else LifecycleCommand.MANAGED_SHUTDOWN
    )
    assert commanded.deployment.lifecycle_command is expected_command
    await execute_fleet(
        execution=store,
        fleet=fleet,
        request=_request(FleetAction.DISARM, mode=FleetModeScope.LIVE, key="disarm"),
    )
    if action is FleetAction.MANAGED_STOP:
        venue.margin = Decimal(1)
    policy = compiled_default_risk_policy().model_copy(
        update={"futures": FuturesRiskPolicy(live_enabled=False, live_derisk_margin_ratio="1.5")}
    )
    try:
        with (
            futures_runtime_scope(FuturesRuntime(contracts, _Observations(()))),
            live_futures_runtime_scope(FuturesLiveRuntime(venue)),
            execution_audit_scope(audit),
        ):
            await _process_one(
                deployment_id=snapshot.deployment.id,
                store=store,
                publication_store=Catalog(_strategy()),
                market_data=MarketDataService(DemoMarketData()),
                paper_broker=PaperBroker(),
                live_broker=venue.broker(),
                quote_reader=None,
                risk_policy=policy,
                portfolio=(commanded,),
                user_feed_store=None,
                memory_store=None,
            )
        current = await store.get_deployment(snapshot.deployment.id)
        assert current.position is None and venue.net == 0
        expected = (
            IntentPurpose.STOP if action is FleetAction.FLATTEN else IntentPurpose.LIQUIDATION
        )
        assert current.intents[-1].purpose is expected
        assert current.intents[-1].price is None
        events = await audit.list_recent()
        reduction = next(event for event in events if event.action == "futures_position_reduction")
        assert "mark_for_journal=130" in (reduction.detail or "")
    finally:
        clear_entry_inhibition_cache()
