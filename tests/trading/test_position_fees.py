"""Held entry-fee allocation uses actual fills, including partial exits and adds."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from thytrader.trading.ledger import (
    MAX_POSITION_FEE_FILLS,
    LedgerFill,
    remaining_position_entry_fees,
)
from thytrader.trading.models import OrderSide, Position, PositionSide

_AT = datetime(2026, 10, 1, tzinfo=UTC)


def _position(side: PositionSide, quantity: str, price: str = "100") -> Position:
    """One exact held book against which fill evidence must reconcile."""
    return Position(
        deployment_id=uuid4(),
        product_id="BTC-USDC",
        quantity=Decimal(quantity),
        entry_price=Decimal(price),
        stop_price=Decimal("90"),
        target_price=None,
        entered_bar=_AT,
        updated_at=_AT,
        side=side,
    )


def _fill(side: OrderSide, quantity: str, fee: str, price: str = "100") -> LedgerFill:
    """One already-applied fill in chronological input order."""
    return LedgerFill(side, Decimal(price), Decimal(quantity), Decimal(fee), _AT)


@pytest.mark.parametrize("side", [PositionSide.LONG, PositionSide.SHORT])
def test_partial_exits_allocate_only_remaining_entry_fees(side: PositionSide) -> None:
    """Exit fees belong to realized inventory; surviving inventory retains its fee share."""
    entry = OrderSide.BUY if side is PositionSide.LONG else OrderSide.SELL
    exit_side = OrderSide.SELL if side is PositionSide.LONG else OrderSide.BUY
    fills = (_fill(entry, "2", "0.4"), _fill(exit_side, "0.5", "7", "105"))
    assert remaining_position_entry_fees(_position(side, "1.5"), fills) == Decimal("0.3")


@pytest.mark.parametrize("side", [PositionSide.LONG, PositionSide.SHORT])
def test_adds_after_partial_exit_accumulate_remaining_costs(side: PositionSide) -> None:
    """A new add contributes its paid fees without restoring fees assigned to exited quantity."""
    entry = OrderSide.BUY if side is PositionSide.LONG else OrderSide.SELL
    exit_side = OrderSide.SELL if side is PositionSide.LONG else OrderSide.BUY
    fills = (
        _fill(entry, "2", "0.4"),
        _fill(exit_side, "1", "0.9", "105"),
        _fill(entry, "1", "0.6", "120"),
    )
    assert remaining_position_entry_fees(_position(side, "2", "110"), fills) == Decimal("0.8")


def test_fee_evidence_must_reconcile_quantity_price_and_entry_window() -> None:
    """Missing or inconsistent fills are unknown, while an observed zero fee is valid."""
    position = _position(PositionSide.LONG, "1")
    fill = _fill(OrderSide.BUY, "1", "0")
    assert remaining_position_entry_fees(position, (fill,)) == Decimal(0)
    assert remaining_position_entry_fees(position, ()) is None
    assert (
        remaining_position_entry_fees(position, (replace(fill, quantity=Decimal("0.5")),)) is None
    )
    assert remaining_position_entry_fees(position, (replace(fill, price=Decimal("99")),)) is None
    before = replace(fill, filled_at=_AT - timedelta(seconds=1))
    assert remaining_position_entry_fees(position, (before, fill)) is None


def test_current_fee_evidence_has_a_finite_fill_limit() -> None:
    """A window above the cap stays unknown even if the truncated prefix could reconcile."""
    fill = _fill(OrderSide.BUY, "1", "0.1")
    fills = (fill,) * MAX_POSITION_FEE_FILLS
    assert remaining_position_entry_fees(
        _position(PositionSide.LONG, str(MAX_POSITION_FEE_FILLS)), fills
    ) == Decimal("100")
    assert (
        remaining_position_entry_fees(
            _position(PositionSide.LONG, str(MAX_POSITION_FEE_FILLS)), (*fills, fill)
        )
        is None
    )
