"""Inventory adoption records, projection, ledger and accounting evidence (ADR 0124)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.adoption_support import (
    ADOPTED_AT,
    MARK_BAR,
    executed_fill,
    live_book,
    records_for,
)
from thytrader.risk.opening_accounting import opening_replay, reconstruct_day_open
from thytrader.trading.adoption import (
    ADOPTION_CLIENT_ORDER_PREFIX,
    adoption_evidence_complete,
    adoption_records,
    project_adoption,
)
from thytrader.trading.exposure import product_exposure, working_entry_notional
from thytrader.trading.fill_ledger import (
    unprojected_inventory_products,
    unsettled_fill_evidence,
)
from thytrader.trading.geometry import entry_bar_bucket
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.models import (
    INVENTORY_OPENING_PURPOSES,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    IntentOrigin,
    IntentPurpose,
    OrderKind,
    OrderSide,
    OrderStatus,
    PositionSide,
    RuntimePhase,
)


def _flat(book_mode: DeploymentMode = DeploymentMode.LIVE) -> DeploymentSnapshot:
    """A flat book snapshot with no history."""
    return DeploymentSnapshot(
        deployment=live_book(mode=book_mode),
        position=None,
        orders=(),
        fills=(),
        intents=(),
    )


def _adopted(
    quantity: Decimal = Decimal(100), mark: Decimal = Decimal("0.2")
) -> DeploymentSnapshot:
    """A live book after one projected adoption."""
    flat = _flat()
    records = records_for(flat.deployment, quantity=quantity, mark=mark)
    projected, _fill = project_adoption(
        flat, records, stop_price=Decimal("0.15"), target_price=Decimal("0.3")
    )
    return projected


def test_inventory_opening_purposes_are_entry_and_adoption() -> None:
    """Only an entry or an adoption opens inventory."""
    assert frozenset({IntentPurpose.ENTRY, IntentPurpose.ADOPTION}) == INVENTORY_OPENING_PURPOSES


def test_adoption_records_are_one_filled_in_kind_buy_with_an_applied_fee_free_fill() -> None:
    """The intent, order and fill never claim a venue id or a venue observation."""
    book = live_book()
    records = records_for(book)
    intent, order, fill = records.intent, records.order, records.fill
    assert intent.purpose is IntentPurpose.ADOPTION and intent.kind is OrderKind.ADOPTION
    assert intent.status is OrderStatus.FILLED and intent.origin is IntentOrigin.HUMAN
    assert intent.candle_starts_at == MARK_BAR and intent.price == Decimal("0.2")
    assert order.kind is OrderKind.ADOPTION and order.side is OrderSide.BUY
    assert order.status is OrderStatus.FILLED and order.filled_quantity == Decimal(100)
    assert order.venue_order_id is None and order.venue_observed_at is None
    assert order.stop_trigger_price is None and order.take_profit_price is None
    assert order.client_order_id.startswith(f"{ADOPTION_CLIENT_ORDER_PREFIX}{book.id}:")
    assert len(order.client_order_id) <= 128 and intent.client_order_id == order.client_order_id
    assert fill.order_id == order.id and fill.fee == 0 and fill.venue_order_id is None
    assert fill.economics_applied_at == fill.filled_at == ADOPTED_AT
    assert len(fill.venue_fill_id) <= 128


@pytest.mark.parametrize(
    ("quantity", "mark", "bar_offset"),
    [
        (Decimal(0), Decimal("0.2"), timedelta(0)),
        (Decimal(100), Decimal(0), timedelta(0)),
        (Decimal("NaN"), Decimal("0.2"), timedelta(0)),
        (Decimal(100), Decimal("0.2"), timedelta(hours=2)),
    ],
)
def test_adoption_records_reject_invalid_amounts_and_future_marks(
    quantity: Decimal, mark: Decimal, bar_offset: timedelta
) -> None:
    """A non-positive amount or a mark bar after the adoption is refused."""
    with pytest.raises(ValueError, match=r"Adoption|mark bar"):
        adoption_records(
            deployment_id=live_book().id,
            product_id="DOGE-USD",
            quantity=quantity,
            mark=mark,
            mark_bar_starts_at=MARK_BAR + bar_offset,
            now=ADOPTED_AT,
            origin=IntentOrigin.AGENT,
        )


def test_projection_opens_a_protected_long_at_the_mark_with_zero_equity() -> None:
    """FLAT goes to OPEN like a buy fill; live cash 0 is debited, so equity at mark is 0."""
    snapshot = _adopted()
    position = snapshot.position
    assert position is not None
    assert position.side is PositionSide.LONG and position.quantity == Decimal(100)
    assert position.entry_price == Decimal("0.2")
    assert position.stop_price == Decimal("0.15") and position.target_price == Decimal("0.3")
    assert position.entered_bar == entry_bar_bucket(ADOPTED_AT, "1h")
    assert position.trail_extreme is None and position.add_count == 1
    deployment = snapshot.deployment
    assert deployment.phase is RuntimePhase.OPEN and deployment.status is DeploymentStatus.RUNNING
    assert deployment.last_evaluated_bar == MARK_BAR
    assert deployment.pending_stop_price is None and deployment.pending_target_price is None
    assert deployment.cash == Decimal("-20")
    ledger = ledger_from_snapshot(snapshot, marks={"DOGE-USD": Decimal("0.2")})
    assert ledger.equity == 0 and ledger.total_fees == 0
    lower = ledger_from_snapshot(snapshot, marks={"DOGE-USD": Decimal("0.18")})
    assert lower.equity == Decimal("-2.00")  # adopted losses are real book losses
    assert not unsettled_fill_evidence(snapshot) and not unprojected_inventory_products(snapshot)


def test_projection_keeps_a_later_evaluation_cursor_and_allows_no_target() -> None:
    """The mark bar never moves the cursor backwards; a stop-only adoption has no target."""
    flat = _flat()
    later = MARK_BAR + timedelta(hours=1)
    flat = replace(flat, deployment=replace(flat.deployment, last_evaluated_bar=later))
    projected, _fill = project_adoption(
        flat, records_for(flat.deployment), stop_price=Decimal("0.15"), target_price=None
    )
    assert projected.deployment.last_evaluated_bar == later
    assert projected.position is not None and projected.position.target_price is None


@pytest.mark.parametrize(
    ("mode", "stop", "target", "message"),
    [
        (DeploymentMode.PAPER, Decimal("0.15"), None, "live-only"),
        (DeploymentMode.LIVE, Decimal("0.2"), None, "stop below"),
        (DeploymentMode.LIVE, Decimal("0.15"), Decimal("0.2"), "target above"),
        (DeploymentMode.LIVE, Decimal(0), None, "positive"),
    ],
)
def test_projection_refuses_paper_and_inverted_protection(
    mode: DeploymentMode, stop: Decimal, target: Decimal | None, message: str
) -> None:
    """Paper has no venue holdings (ADR 0124), and an adopted long needs real protection."""
    flat = _flat(mode)
    with pytest.raises(ValueError, match=message):
        project_adoption(flat, records_for(flat.deployment), stop_price=stop, target_price=target)


def test_projection_refuses_an_occupied_book_and_foreign_records() -> None:
    """Adoption never scales into an open position or writes another book's records."""
    adopted = _adopted()
    with pytest.raises(ValueError, match="flat product book"):
        project_adoption(
            adopted,
            records_for(adopted.deployment),
            stop_price=Decimal("0.15"),
            target_price=None,
        )
    flat = _flat()
    with pytest.raises(ValueError, match="not an adoption for this book"):
        project_adoption(
            flat, records_for(live_book()), stop_price=Decimal("0.15"), target_price=None
        )


def test_adopt_then_add_then_partial_exit_is_not_unprojected_inventory() -> None:
    """The adoption anchors inventory; anchoring on the later add raised a false fault."""
    added = executed_fill(
        _adopted(),
        purpose=IntentPurpose.ENTRY,
        side=OrderSide.BUY,
        quantity=Decimal(50),
        price=Decimal("0.21"),
        minutes=5,
    )
    exited = executed_fill(
        added,
        purpose=IntentPurpose.STOP,
        side=OrderSide.SELL,
        quantity=Decimal(80),
        price=Decimal("0.22"),
        minutes=10,
    )
    assert exited.position is not None and exited.position.quantity == Decimal(70)
    assert unprojected_inventory_products(exited) == ()


def test_unprojected_adopted_inventory_is_still_detected() -> None:
    """An adoption fill without its position is unprojected inventory, not a clean book."""
    snapshot = replace(_adopted(), position=None, positions=())
    assert unprojected_inventory_products(snapshot) == ("DOGE-USD",)


def test_adoption_evidence_requires_one_applied_fill_covering_the_order() -> None:
    """Missing, unapplied or partial fills and a venue id are incomplete evidence."""
    snapshot = _adopted()
    order = next(item for item in snapshot.orders if item.kind is OrderKind.ADOPTION)
    assert adoption_evidence_complete(snapshot, order)
    assert not adoption_evidence_complete(replace(snapshot, fills=()), order)
    unapplied = tuple(replace(fill, economics_applied_at=None) for fill in snapshot.fills)
    assert not adoption_evidence_complete(replace(snapshot, fills=unapplied), order)
    partial = tuple(replace(fill, quantity=Decimal(40)) for fill in snapshot.fills)
    assert not adoption_evidence_complete(replace(snapshot, fills=partial), order)
    assert not adoption_evidence_complete(snapshot, replace(order, venue_order_id="v"))


def test_intraday_adoption_opening_equity_is_the_initial_funding() -> None:
    """The adoption fill qualifies as ADR 0120 opening evidence; same-day equity stays 0."""
    snapshot = _adopted()
    as_of = ADOPTED_AT + timedelta(hours=1)
    replay = opening_replay(snapshot, as_of=as_of)
    assert replay is not None and replay.day_cash_change == Decimal("-20")
    assert replay.midnight_quantities == {}
    opening = reconstruct_day_open(snapshot, as_of=as_of)
    assert opening is not None and opening.equity == 0 and opening.marks == ()


def test_adopted_exposure_is_quantity_at_the_mark_and_reserves_no_entry_quote() -> None:
    """The FILLED adoption is no working entry; its position is cost basis at the mark."""
    snapshot = _adopted()
    assert working_entry_notional(snapshot, "DOGE-USD") == 0
    assert product_exposure(snapshot, "DOGE-USD") == Decimal("20.0")
