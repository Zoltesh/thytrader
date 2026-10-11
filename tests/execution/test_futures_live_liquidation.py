"""Live margin supervision runs before bars and ignores entry authority."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_paper_futures_books import _state
from thytrader.execution.futures_live_liquidation import supervise_live_futures
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentStatus, IntentPurpose, LifecycleCommand


@pytest.mark.anyio
@pytest.mark.parametrize("available, exits", [("30", False), ("29.999999999999999999999999", True)])
@pytest.mark.parametrize("stopped", [False, True])
async def test_margin_threshold_strict_boundary_ignores_entry_controls(
    available: str, exits: bool, stopped: bool
) -> None:
    """A strict ratio breach closes with live disabled, killswitch on and managed stop."""
    store = InMemoryExecutionStore()
    venue = FakeVenue()
    snapshot = await open_live_book(store, venue)
    venue.margin = Decimal(available)
    venue.killswitch = True
    if stopped:
        await store.save_deployment(
            replace(
                snapshot.deployment,
                status=DeploymentStatus.STOPPED,
                lifecycle_command=LifecycleCommand.STOP_NEW_ENTRIES,
            )
        )
        snapshot = await store.get_deployment(snapshot.deployment.id)
    policy = compiled_default_risk_policy().model_copy(
        update={"futures": FuturesRiskPolicy(live_enabled=False, live_derisk_margin_ratio="1.5")}
    )
    current = await supervise_live_futures(
        snapshot,
        state=_state(snapshot),
        account=await venue.read_account(frozenset()),
        policy=policy,
        broker=venue.broker(),
        store=store,
        now=venue.now,
    )
    assert (current.position is None) is exits
    assert any(intent.purpose is IntentPurpose.LIQUIDATION for intent in current.intents) is exits
    if stopped:
        assert current.deployment.status is DeploymentStatus.STOPPED
