"""Managed base claims and the adoptable quantity (ADR 0124)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.adoption_support import (
    adopted_book,
    balance,
    entry_intent,
    live_book,
    working_order,
)
from thytrader.trading.inventory_claims import (
    BaseUnresolvedReason,
    base_availability,
    managed_base_claims,
    unmanaged_available_base,
)
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    IntentPurpose,
    OrderKind,
    OrderSide,
    OrderStatus,
)

if TYPE_CHECKING:
    from thytrader.exchanges.models import ExchangeBalance

pytestmark = pytest.mark.anyio


async def _long_100() -> DeploymentSnapshot:
    """A live DOGE book that owns a 100 DOGE long."""
    return await adopted_book(InMemoryExecutionStore())


def _with(
    snapshot: DeploymentSnapshot,
    *,
    side: OrderSide,
    purpose: IntentPurpose,
    quantity: Decimal = Decimal(30),
    filled: Decimal = Decimal(0),
    status: OrderStatus = OrderStatus.OPEN,
    kind: OrderKind = OrderKind.POST_ONLY_LIMIT,
    ordered: bool = True,
    minutes: int = 1,
) -> DeploymentSnapshot:
    """Add one intent (and its working order unless ``ordered`` is False)."""
    intent = entry_intent(
        snapshot.deployment, side=side, quantity=quantity, purpose=purpose, minutes=minutes
    )
    orders = snapshot.orders
    if ordered:
        orders = (*orders, working_order(intent, kind=kind, status=status, filled=filled))
    return replace(snapshot, intents=(*snapshot.intents, intent), orders=orders)


async def test_a_protected_long_is_subtracted_once() -> None:
    """Coinbase ``available`` already excludes the protective sell's hold."""
    protected = _with(
        await _long_100(),
        side=OrderSide.SELL,
        purpose=IntentPurpose.BRACKET,
        quantity=Decimal(100),
        kind=OrderKind.TRIGGER_BRACKET,
    )
    claims = managed_base_claims((protected,), "DOGE")
    assert claims.claimed == Decimal(100)
    figures = base_availability((balance("DOGE", "50", hold="100"),), claims)
    assert figures.total == Decimal(150) and figures.unmanaged == Decimal(50)
    assert figures.adoptable == Decimal(50) and figures.reasons == ()


async def test_an_unprotected_long_is_not_available_to_anyone_else() -> None:
    """Raw available would hand a short or an adoption another book's 100 DOGE."""
    rows = (balance("DOGE", "150"),)
    assert unmanaged_available_base(rows, (await _long_100(),), "DOGE") == Decimal(50)
    assert unmanaged_available_base(rows, (), "DOGE") == Decimal(150)


async def test_working_opening_orders_and_unordered_entries_are_claimed() -> None:
    """Unfilled buys and short-entry sells, and a mid-submit entry intent, all claim base."""
    book = await _long_100()
    book = _with(book, side=OrderSide.BUY, purpose=IntentPurpose.ENTRY, quantity=Decimal(20))
    book = _with(book, side=OrderSide.SELL, purpose=IntentPurpose.ENTRY, minutes=2)
    book = _with(book, side=OrderSide.BUY, purpose=IntentPurpose.ENTRY, ordered=False, minutes=3)
    claims = managed_base_claims((book,), "DOGE")
    assert claims.managed_long == Decimal(100)
    assert claims.working_buys == Decimal(20) + Decimal(30)
    assert claims.working_short_entry_sells == Decimal(30)
    figures = base_availability((balance("DOGE", "300"),), claims)
    assert figures.adoptable == Decimal(300) - Decimal(180)


async def test_short_covers_and_finished_orders_claim_nothing() -> None:
    """A cover buy restores unmanaged base; filled and canceled orders hold no remainder."""
    book = await _long_100()
    book = _with(book, side=OrderSide.BUY, purpose=IntentPurpose.STOP)
    book = _with(
        book,
        side=OrderSide.BUY,
        purpose=IntentPurpose.ENTRY,
        status=OrderStatus.CANCELED,
        minutes=2,
    )
    book = _with(
        book,
        side=OrderSide.BUY,
        purpose=IntentPurpose.ENTRY,
        filled=Decimal(30),
        status=OrderStatus.FILLED,
        minutes=3,
    )
    claims = managed_base_claims((book,), "DOGE")
    assert claims.working_buys == 0 and claims.working_short_entry_sells == 0


async def test_paper_books_and_other_bases_claim_nothing_but_quotes_share_a_base() -> None:
    """Paper owns no venue base; DOGE-USD and DOGE-USDC books both claim DOGE."""
    store = InMemoryExecutionStore()
    paper = await adopted_book(store)
    paper = replace(paper, deployment=replace(paper.deployment, mode=DeploymentMode.PAPER))
    usdc = await adopted_book(store, book=live_book(product_id="DOGE-USDC"))
    btc = await adopted_book(
        store,
        book=live_book(product_id="BTC-USD"),
        quantity=Decimal(1),
        mark=Decimal(100),
        stop_price=Decimal(90),
        target_price=None,
    )
    assert managed_base_claims((paper, usdc, btc), "DOGE").managed_long == Decimal(100)
    assert managed_base_claims((paper, usdc, btc), "BTC").managed_long == Decimal(1)


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("unknown_order", BaseUnresolvedReason.UNKNOWN_ORDER),
        ("unsettled_fill", BaseUnresolvedReason.FILL_ECONOMICS_UNSETTLED),
        ("incomplete", BaseUnresolvedReason.ACCOUNTING_UNRESOLVED),
    ],
)
async def test_unknown_book_evidence_leaves_the_base_unresolved(
    fault: str, reason: BaseUnresolvedReason
) -> None:
    """Unknown is never zero: the adoptable quantity is None and the reason is named."""
    book = await _long_100()
    if fault == "unknown_order":
        book = _with(
            book, side=OrderSide.BUY, purpose=IntentPurpose.ENTRY, status=OrderStatus.UNKNOWN
        )
    elif fault == "unsettled_fill":
        book = replace(
            book, fills=tuple(replace(fill, economics_applied_at=None) for fill in book.fills)
        )
    else:
        book = replace(book, accounting_complete=False)
    figures = base_availability((balance("DOGE", "500"),), managed_base_claims((book,), "DOGE"))
    assert figures.adoptable is None and figures.unmanaged is None
    assert reason in figures.reasons


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ((), BaseUnresolvedReason.BALANCE_MISSING),
        ((balance("BTC", "1"),), BaseUnresolvedReason.BALANCE_MISSING),
        (
            (balance("DOGE", "10"), balance("DOGE", "10")),
            BaseUnresolvedReason.DUPLICATE_BALANCE_ROWS,
        ),
    ],
)
def test_a_missing_or_duplicate_balance_row_is_unknown(
    rows: tuple[ExchangeBalance, ...], reason: BaseUnresolvedReason
) -> None:
    """A missing row is not a zero balance, and duplicates cannot be chosen between."""
    figures = base_availability(
        rows,
        managed_base_claims((), "DOGE"),
    )
    assert figures.adoptable is None and figures.reasons == (reason,)


async def test_adoptable_rounds_down_and_is_never_negative() -> None:
    """Round down to the base increment; claims above the total adopt nothing."""
    claims = managed_base_claims((), "DOGE")
    assert (
        base_availability((balance("DOGE", "12.987"),), claims, base_increment=Decimal("0.01"))
    ).adoptable == Decimal("12.98")
    over = managed_base_claims((await _long_100(),), "DOGE")
    assert base_availability((balance("DOGE", "40"),), over).adoptable == 0
