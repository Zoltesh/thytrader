"""Catalog-sized reductions and managed repair recovery use real adapter and ledger paths."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from thytrader.execution.futures_live import FuturesLiveRuntime, live_futures_runtime_scope
from thytrader.execution.futures_reduction import reduce_live_futures_position
from thytrader.execution_worker.futures_step import supervised_live_futures
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentStatus, IntentPurpose

if TYPE_CHECKING:
    from collections.abc import Mapping


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("size", "contracts", "allowed"),
    [
        ("0.005", "4", True),
        ("0.02", "1", True),
        ("0.03", "1", False),
        ("0.005", "2", False),
    ],
)
async def test_exit_uses_catalog_whole_contracts_or_refuses_drift(
    size: str,
    contracts: str,
    allowed: bool,
) -> None:
    """Changing units never opens guessed exposure or sends the bound contract count."""
    venue = FakeVenue()
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    venue.size = Decimal(size)
    venue.net = Decimal(contracts)
    sends = len(venue.creates)
    reduced = await reduce_live_futures_position(
        snapshot,
        account=await venue.read_account(frozenset()),
        broker=venue.broker(),
        store=store,
        purpose=IntentPurpose.STOP,
        now=venue.now,
    )
    if allowed:
        assert reduced.position is None and venue.net == 0
        body = venue.creates[-1][2]
        assert isinstance(body, dict) and ("size", contracts) in body.items()
    else:
        assert len(venue.creates) == sends
        assert reduced.position == snapshot.position
        assert "FUTURES_REDUCTION_UNPROVEN" in (reduced.deployment.mismatch_detail or "")


class ProcessCrashVenue(FakeVenue):
    """Crash outside the adapter error handler before/after an accepted repair POST."""

    crash: bool = False
    after_post: bool = False

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Raw dictionaries model the untrusted transport, never production acceptance."""
        if self.crash and path.endswith("/close_position"):
            self.crash = False
            if self.after_post:
                super().post(path, data)
            raise KeyboardInterrupt("synthetic repair process crash")
        return super().post(path, data)


@pytest.mark.anyio
@pytest.mark.parametrize("after_post", [False, True])
async def test_repair_restart_preserves_projection_and_never_duplicates(
    monkeypatch: pytest.MonkeyPatch,
    after_post: bool,
) -> None:
    """Restart applies accepted repair fills once; an unproven send stays UNKNOWN forever."""
    venue = ProcessCrashVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    assert snapshot.position is not None
    intended = replace(snapshot.position, quantity=Decimal("0.01"))
    await store.save_position(intended, deployment_id=snapshot.deployment.id)
    venue.after_post = after_post
    venue.crash = True

    async def step() -> None:
        """Run the same reconciliation-before-I9 orchestration used by the worker."""
        current = await store.get_deployment(snapshot.deployment.id)
        async with supervised_live_futures(
            current,
            state=None,
            broker=venue.broker(),
            store=store,
            policy=compiled_default_risk_policy(),
            portfolio=(current,),
        ):
            pass

    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        await step()
        venue.advance()
        with pytest.raises(KeyboardInterrupt, match="repair process"):
            await step()
    sends = len(venue.creates)
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        for _ in range(3):
            venue.advance()
            await step()
    current = await store.get_deployment(snapshot.deployment.id)
    assert len(venue.creates) == sends
    assert current.position == intended
    assert current.deployment.status is DeploymentStatus.PAUSED
    assert len(current.fills) == (2 if after_post else 1)
    assert all(fill.economics_applied_at is not None for fill in current.fills)
    assert venue.net == (1 if after_post else 2)
    assert current.deployment.cash == (Decimal("-1.60") if after_post else Decimal("-2.75"))
