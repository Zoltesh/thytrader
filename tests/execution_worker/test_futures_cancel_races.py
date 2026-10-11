"""Adversarial cancel races and ownership controls use the actual fenced futures worker."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from tests.execution.test_paper_futures_books import _PERP, _binding, _Observations
from tests.execution_worker.futures_safety_support import cancels, closes, protect, step, stop_book
from thytrader.execution.futures_live import FuturesLiveRuntime, live_futures_runtime_scope
from thytrader.execution.futures_paper import FuturesRuntime, futures_runtime_scope
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import LifecycleCommand

if TYPE_CHECKING:
    from collections.abc import Mapping


class CancelRaceVenue(FakeVenue):
    """Canonical synthetic fills arrive while cancellation is in flight."""

    race_contracts: Decimal | None = None

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Apply the race before returning the adapter's normal cancel confirmation."""
        if path.endswith("/batch_cancel") and self.race_contracts is not None:
            assert data is not None
            ids = data["order_ids"]
            assert isinstance(ids, list)
            self.fill(str(ids[0]), self.race_contracts)
            self.race_contracts = None
        return super().post(path, data)


@pytest.mark.anyio
@pytest.mark.parametrize("contracts", [1, 2, 3])
async def test_cancel_race_requires_new_position_proof(
    monkeypatch: pytest.MonkeyPatch,
    contracts: int,
) -> None:
    """Partial, flat and flipped race outcomes never authorize a same-sample reduction."""
    venue = CancelRaceVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    snapshot, _ = await protect(store, venue, snapshot)
    await stop_book(store, snapshot, LifecycleCommand.FLATTEN)
    bindings = InMemoryFuturesContractStore()
    await bindings.bind_contract(_binding(snapshot))
    venue.race_contracts = Decimal(contracts)
    with (
        futures_runtime_scope(FuturesRuntime(bindings, _Observations(()))),
        live_futures_runtime_scope(FuturesLiveRuntime(venue)),
    ):
        current = await step(store, venue, snapshot.deployment.id)
        assert cancels(venue) == 1 and closes(venue) == 0
        assert current.deployment.futures_flat_confirmation_after is not None
        assert len(current.fills) == 2
        for _ in range(4):
            venue.advance()
            current = await step(store, venue, snapshot.deployment.id)
    assert venue.net == 0 and current.position is None
    assert closes(venue) == (0 if contracts == 2 else 1)
    assert current.deployment.futures_flat_confirmation_after is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "block",
    ["unknown_orders", "foreign_order", "foreign_position", "identity", "unconfirmed", "failed"],
)
async def test_cancel_authority_never_infers_ownership_or_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    block: str,
) -> None:
    """Unknown ownership causes no cancel; failed/unconfirmed cancels cause no close."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    snapshot, stop = await protect(store, venue, snapshot)
    await stop_book(store, snapshot, LifecycleCommand.FLATTEN)
    bindings = InMemoryFuturesContractStore()
    await bindings.bind_contract(_binding(snapshot))
    if block == "unknown_orders":
        venue.external = None
    elif block == "foreign_order":
        venue.external = frozenset({_PERP})
    elif block == "foreign_position":
        venue.net += 1
    elif block == "identity":
        await store.save_order(replace(stop, client_order_id="synthetic-foreign-client"))
    elif block == "unconfirmed":
        venue.cancel_confirmed = False
    with (
        futures_runtime_scope(FuturesRuntime(bindings, _Observations(()))),
        live_futures_runtime_scope(FuturesLiveRuntime(venue)),
    ):
        for _ in range(3):
            venue.advance()
            if block == "failed":
                venue.timeout_before_post = True
            current = await step(store, venue, snapshot.deployment.id)
    assert closes(venue) == 0
    assert current.position is not None
    if block == "unconfirmed":
        assert cancels(venue) == 1, "accepted cancels are rechecked, never spammed"
    elif block == "failed":
        assert cancels(venue) == 3
    else:
        assert cancels(venue) == 0
