"""In-memory inventory adoption store, base lock and the live short-sale base (ADR 0124)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.adoption_support import (
    adopted_book,
    adoption_write,
    balance,
    balance_reader,
    entry_intent,
    live_book,
)
from thytrader.api.routes.discretionary_orders import _unmanaged_base_available
from thytrader.execution_worker.live_sizing import _short_sellable_base
from thytrader.trading.adoption_write import (
    ADOPTION_BALANCE_UNAVAILABLE,
    ADOPTION_QUANTITY_UNAVAILABLE,
    ADOPTION_REFUSED,
    AdoptionRefusedError,
)
from thytrader.trading.inventory_claims import ADOPTION_BASE_UNRESOLVED
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.memory_adoption import InMemoryInventoryAdoptionStore
from thytrader.trading.models import (
    DeploymentKind,
    DeploymentMode,
    ExecutionConflictError,
    IntentPurpose,
    OrderKind,
    RuntimePhase,
)
from thytrader.trading.store import InventoryAdoptionStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.exchanges.models import ExchangeBalance
    from thytrader.trading.entry_latch import InhibitionSnapshot

pytestmark = pytest.mark.anyio


class _InhibitedLatch:
    """A fleet latch that inhibits every mode."""

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        """No lock is needed for a constant latch."""
        yield

    def raise_if_inhibited(self, mode: str, *, action: str) -> None:
        """Always refuse."""
        raise ExecutionConflictError(f"ENTRY_INHIBITED: {mode} {action} refused")

    async def read_inhibition(self) -> InhibitionSnapshot:
        """Not read by these tests."""
        raise AssertionError("unused")


class _Reader:
    """A venue reader that can block until released, and records each call."""

    def __init__(self, *rows: ExchangeBalance, gate: asyncio.Event | None = None) -> None:
        """Bind the rows and an optional release event."""
        self.rows = rows
        self.gate = gate
        self.called = asyncio.Event()

    async def __call__(self) -> tuple[ExchangeBalance, ...]:
        """Signal the call, wait for release, then return the rows."""
        self.called.set()
        if self.gate is not None:
            await self.gate.wait()
        return self.rows


async def _fresh() -> tuple[InMemoryExecutionStore, InMemoryInventoryAdoptionStore]:
    """An execution store with one flat live DOGE book, and its adoption store."""
    store = InMemoryExecutionStore()
    await store.create_deployment(live_book())
    return store, InMemoryInventoryAdoptionStore(store)


async def test_adoption_commits_records_position_and_revision_together() -> None:
    """One call writes the intent, FILLED order, applied fill, position and next revision."""
    store, adoption = await _fresh()
    book = next(iter(store.deployments.values()))
    assert isinstance(adoption, InventoryAdoptionStore)
    commit = await adoption.adopt_inventory(
        adoption_write(book, quantity=Decimal(60)),
        read_balances=balance_reader(balance("DOGE", "100")),
    )
    snapshot = commit.snapshot
    assert [intent.purpose for intent in snapshot.intents] == [IntentPurpose.ADOPTION]
    assert [order.kind for order in snapshot.orders] == [OrderKind.ADOPTION]
    assert snapshot.fills[0].economics_applied_at is not None
    assert snapshot.position is not None and snapshot.position.quantity == Decimal(60)
    assert snapshot.deployment.phase is RuntimePhase.OPEN
    assert snapshot.deployment.revision == book.revision + 1
    assert commit.availability.adoptable == Decimal(100)


async def test_all_adopts_every_unmanaged_unit_and_more_is_refused() -> None:
    """None means all; a second book can no longer adopt the claimed coins."""
    store, adoption = await _fresh()
    book = next(iter(store.deployments.values()))
    reader = balance_reader(balance("DOGE", "100.9"))
    commit = await adoption.adopt_inventory(
        adoption_write(book, quantity=None), read_balances=reader
    )
    assert commit.records.order.quantity == Decimal(100)
    other = await store.create_deployment(live_book(kind=DeploymentKind.DISCRETIONARY))
    with pytest.raises(AdoptionRefusedError) as refused:
        await adoption.adopt_inventory(
            adoption_write(other, quantity=Decimal(1)), read_balances=reader
        )
    assert refused.value.code == ADOPTION_QUANTITY_UNAVAILABLE
    assert (await store.get_deployment(other.id)).orders == ()


@pytest.mark.parametrize(
    ("reader_rows", "quantity", "code"),
    [
        ((), Decimal(10), ADOPTION_BASE_UNRESOLVED),
        ((balance("DOGE", "5"),), Decimal(10), ADOPTION_QUANTITY_UNAVAILABLE),
        ((balance("DOGE", "50"),), Decimal("10.5"), ADOPTION_QUANTITY_UNAVAILABLE),
        ((balance("DOGE", "50"),), Decimal(0), ADOPTION_QUANTITY_UNAVAILABLE),
    ],
)
async def test_refusals_write_nothing(
    reader_rows: tuple[ExchangeBalance, ...], quantity: Decimal, code: str
) -> None:
    """A missing balance, too little base or a fractional quantity leaves the book flat."""
    store, adoption = await _fresh()
    book = next(iter(store.deployments.values()))
    with pytest.raises(AdoptionRefusedError) as refused:
        await adoption.adopt_inventory(
            adoption_write(book, quantity=quantity), read_balances=balance_reader(*reader_rows)
        )
    assert refused.value.code == code
    after = await store.get_deployment(book.id)
    assert after.intents == () and after.orders == () and after.fills == ()
    assert after.position is None and after.deployment.revision == book.revision


async def test_an_unreadable_venue_and_an_occupied_book_are_refused() -> None:
    """A venue failure is unknown, not zero; adoption never scales into a position."""
    store, adoption = await _fresh()
    book = next(iter(store.deployments.values()))

    async def broken() -> tuple[ExchangeBalance, ...]:
        """Fail like a venue outage."""
        raise OSError("venue down")

    with pytest.raises(AdoptionRefusedError) as unreadable:
        await adoption.adopt_inventory(adoption_write(book), read_balances=broken)
    assert unreadable.value.code == ADOPTION_BALANCE_UNAVAILABLE
    reader = balance_reader(balance("DOGE", "500"))
    await adoption.adopt_inventory(adoption_write(book), read_balances=reader)
    with pytest.raises(AdoptionRefusedError) as occupied:
        await adoption.adopt_inventory(adoption_write(book), read_balances=reader)
    assert occupied.value.code == ADOPTION_REFUSED


async def test_a_new_book_is_inserted_with_its_adoption_or_not_at_all() -> None:
    """Strategy start with adoption: the book and the adoption commit together."""
    store = InMemoryExecutionStore()
    adoption = InMemoryInventoryAdoptionStore(store)
    book = live_book()
    with pytest.raises(AdoptionRefusedError):
        await adoption.adopt_inventory(
            adoption_write(book, new=True), read_balances=balance_reader(balance("DOGE", "5"))
        )
    assert book.id not in store.deployments
    commit = await adoption.adopt_inventory(
        adoption_write(book, new=True), read_balances=balance_reader(balance("DOGE", "100"))
    )
    assert commit.snapshot.position is not None and book.id in store.deployments


async def test_strategy_adoption_respects_the_fleet_latch() -> None:
    """A latched fleet refuses a new adopting book; nothing is written."""
    store = InMemoryExecutionStore()
    store.bind_entry_gate(_InhibitedLatch())
    adoption = InMemoryInventoryAdoptionStore(store)
    book = live_book()
    with pytest.raises(ExecutionConflictError, match="ENTRY_INHIBITED"):
        await adoption.adopt_inventory(
            adoption_write(book, new=True, respect_entry_latch=True),
            read_balances=balance_reader(balance("DOGE", "100")),
        )
    assert store.deployments == {} and store.intents == {}


async def test_concurrent_adoptions_on_one_base_serialise_and_only_one_fits() -> None:
    """Two books each asking for 80 of 100 DOGE: the base lock lets exactly one through."""
    store = InMemoryExecutionStore()
    adoption = InMemoryInventoryAdoptionStore(store)
    first = await store.create_deployment(live_book())
    second = await store.create_deployment(live_book(kind=DeploymentKind.DISCRETIONARY))
    reader = balance_reader(balance("DOGE", "100"))
    results = await asyncio.gather(
        adoption.adopt_inventory(adoption_write(first, quantity=Decimal(80)), read_balances=reader),
        adoption.adopt_inventory(
            adoption_write(second, quantity=Decimal(80)), read_balances=reader
        ),
        return_exceptions=True,
    )
    refused = [item for item in results if isinstance(item, AdoptionRefusedError)]
    assert len(refused) == 1 and refused[0].code == ADOPTION_QUANTITY_UNAVAILABLE
    assert len(results) - len(refused) == 1


async def test_a_live_entry_intent_waits_for_an_adoption_in_progress() -> None:
    """The entry lands after the adoption commits, so the adoption never misses it."""
    store = InMemoryExecutionStore()
    adoption = InMemoryInventoryAdoptionStore(store)
    book = await store.create_deployment(live_book())
    trader = await store.create_deployment(live_book(kind=DeploymentKind.DISCRETIONARY))
    release = asyncio.Event()
    reader = _Reader(balance("DOGE", "100"), gate=release)
    adopting = asyncio.create_task(
        adoption.adopt_inventory(adoption_write(book, quantity=Decimal(70)), read_balances=reader)
    )
    await reader.called.wait()
    entering = asyncio.create_task(store.save_intent(entry_intent(trader)))
    await asyncio.sleep(0.05)
    assert not entering.done()
    release.set()
    await adopting
    await entering
    claims_after = await _short_sellable_base(
        _Balances(balance("DOGE", "100")),
        "DOGE",
        portfolio=(await store.get_deployment(book.id),),
        current=await store.get_deployment(trader.id),
    )
    assert claims_after == Decimal(0)  # 100 - 70 adopted - 30 pending entry


async def test_paper_entries_never_wait_for_the_live_base_lock() -> None:
    """Only live entries take the base lock."""
    store = InMemoryExecutionStore()
    paper = await store.create_deployment(live_book(mode=DeploymentMode.PAPER))
    async with store.inventory_lock(DeploymentMode.LIVE, "DOGE"):
        await asyncio.wait_for(store.save_intent(entry_intent(paper)), timeout=1)


class _Balances:
    """An ``ExchangeAccount`` (and worker quote reader) double with fixed balances."""

    def __init__(self, *rows: ExchangeBalance) -> None:
        """Bind the rows."""
        self.rows = rows

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Return the rows."""
        return self.rows

    async def get_permissions(self) -> tuple[str, ...]:
        """Unused by the short-sale base read."""
        raise AssertionError("unused")

    async def get_usd_price(self, currency: str) -> Decimal | None:
        """Unused by the short-sale base read."""
        raise AssertionError(currency)

    async def get_fee_profile(self) -> FeeProfile:
        """Unused by the short-sale base read."""
        raise AssertionError("unused")


async def test_live_short_base_excludes_an_unprotected_managed_long() -> None:
    """Regression: raw available let a short sell base another book's long owns."""
    store = InMemoryExecutionStore()
    owner = await adopted_book(store)
    trader = await store.create_deployment(live_book(kind=DeploymentKind.DISCRETIONARY))
    reader = _Balances(balance("DOGE", "150"))
    route = await _unmanaged_base_available(
        reader,
        store=store,
        mode=DeploymentMode.LIVE,
        product_id="DOGE-USD",
    )
    worker = await _short_sellable_base(
        reader, "DOGE", portfolio=(owner,), current=await store.get_deployment(trader.id)
    )
    assert route == worker == Decimal(50)
    assert (
        await _unmanaged_base_available(
            reader,
            store=store,
            mode=DeploymentMode.PAPER,
            product_id="DOGE-USD",
        )
    ) is None


async def test_live_short_base_is_unknown_when_a_book_is_unresolved() -> None:
    """An unresolved book on the base refuses the short instead of guessing."""
    store = InMemoryExecutionStore()
    owner = await adopted_book(store)
    unresolved = replace(owner, accounting_complete=False)
    worker = await _short_sellable_base(
        _Balances(balance("DOGE", "150")), "DOGE", portfolio=(unresolved,), current=unresolved
    )
    assert worker is None
