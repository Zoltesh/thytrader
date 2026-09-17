"""Fill-ledger PnL from recorded paper/live fills without inventing missing marks."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from thytrader.execution.ledger import (
    PAPER_MAKER_FEE_RATE,
    PAPER_TAKER_FEE_RATE,
    LedgerFill,
    effective_paper_fee_rates,
    ledger_from_snapshot,
    mark_deployment_ledger,
    paper_fill_fee,
    resolve_paper_fee_schedule,
)
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
)
from thytrader.research.indicators import canonical_decimal


def _at(hour: int) -> datetime:
    """Return a UTC instant on 2026-01-01."""
    return datetime(2026, 1, 1, hour, tzinfo=UTC)


def test_resolve_paper_fee_schedule_defaults_validates_and_rejects_live() -> None:
    """Omitted paper rates become documented defaults; live never stores modeled rates."""
    assert resolve_paper_fee_schedule(live=False, maker_fee_rate=None, taker_fee_rate=None) == (
        PAPER_MAKER_FEE_RATE,
        PAPER_TAKER_FEE_RATE,
    )
    custom = resolve_paper_fee_schedule(
        live=False, maker_fee_rate=Decimal("0.0025"), taker_fee_rate=Decimal("0.004")
    )
    assert custom == (Decimal("0.0025"), Decimal("0.004"))
    assert resolve_paper_fee_schedule(live=True, maker_fee_rate=None, taker_fee_rate=None) == (
        None,
        None,
    )
    with pytest.raises(ValueError, match="Live deployments"):
        resolve_paper_fee_schedule(
            live=True, maker_fee_rate=Decimal("0.001"), taker_fee_rate=Decimal("0.002")
        )
    with pytest.raises(ValueError, match="both maker_fee_rate"):
        resolve_paper_fee_schedule(live=False, maker_fee_rate=Decimal("0.001"), taker_fee_rate=None)
    with pytest.raises(ValueError, match=r"\[0, 0.1\]"):
        resolve_paper_fee_schedule(
            live=False, maker_fee_rate=Decimal("0.2"), taker_fee_rate=Decimal("0.2")
        )
    with pytest.raises(ValueError, match="must not exceed"):
        resolve_paper_fee_schedule(
            live=False, maker_fee_rate=Decimal("0.004"), taker_fee_rate=Decimal("0.002")
        )
    assert effective_paper_fee_rates(None, None) == (
        PAPER_MAKER_FEE_RATE,
        PAPER_TAKER_FEE_RATE,
    )


def test_paper_fill_fee_uses_maker_on_post_only_and_taker_on_marketable() -> None:
    """The documented paper schedule is 0.001 maker and 0.002 taker."""
    maker = paper_fill_fee(
        kind=OrderKind.POST_ONLY_LIMIT, price=Decimal("100"), quantity=Decimal("2")
    )
    taker = paper_fill_fee(kind=OrderKind.MARKETABLE, price=Decimal("100"), quantity=Decimal("2"))
    assert maker == Decimal("0.2")
    assert taker == Decimal("0.4")
    assert maker == Decimal("100") * Decimal("2") * PAPER_MAKER_FEE_RATE
    custom = paper_fill_fee(
        kind=OrderKind.POST_ONLY_LIMIT,
        price=Decimal("100"),
        quantity=Decimal("2"),
        maker_fee_rate=Decimal("0.0025"),
        taker_fee_rate=Decimal("0.004"),
    )
    assert custom == Decimal("0.5")


def test_round_trip_with_fees_reports_realized_pnl_and_one_trade() -> None:
    """A buy then sell with fees is one round trip whose net matches cash minus start."""
    buy = LedgerFill(
        side=OrderSide.BUY,
        price=Decimal("100"),
        quantity=Decimal("1"),
        fee=Decimal("0.1"),
        filled_at=_at(1),
    )
    sell = LedgerFill(
        side=OrderSide.SELL,
        price=Decimal("110"),
        quantity=Decimal("1"),
        fee=Decimal("0.11"),
        filled_at=_at(2),
    )
    starting = Decimal("10000")
    cash = starting - Decimal("100") - Decimal("0.1") + Decimal("110") - Decimal("0.11")
    ledger = mark_deployment_ledger(
        starting_cash=starting,
        cash=cash,
        fills=(buy, sell),
        position_quantity=Decimal("0"),
        position_entry_price=None,
        mark_price=None,
    )
    assert ledger.trade_count == 1
    assert ledger.realized_net_pnl == Decimal("9.79")
    assert ledger.unrealized_net_pnl == Decimal("0")
    assert ledger.total_net_pnl == Decimal("9.79")
    assert ledger.total_fees == Decimal("0.21")
    assert ledger.mark_complete is True
    assert ledger.total_net_pnl_text() == canonical_decimal(Decimal("9.79"))
    assert ledger.total_return_fraction == Decimal("9.79") / starting


def test_zero_fees_increase_realized_pnl_versus_the_same_prices() -> None:
    """Fee-free fills are comparable to the fee-carrying pair at the same prices."""
    buy = LedgerFill(
        side=OrderSide.BUY,
        price=Decimal("100"),
        quantity=Decimal("1"),
        fee=Decimal("0"),
        filled_at=_at(1),
    )
    sell = LedgerFill(
        side=OrderSide.SELL,
        price=Decimal("110"),
        quantity=Decimal("1"),
        fee=Decimal("0"),
        filled_at=_at(2),
    )
    ledger = mark_deployment_ledger(
        starting_cash=Decimal("10000"),
        cash=Decimal("10010"),
        fills=(buy, sell),
        position_quantity=Decimal("0"),
        position_entry_price=None,
        mark_price=None,
    )
    assert ledger.realized_net_pnl == Decimal("10")
    assert ledger.total_net_pnl == Decimal("10")
    assert ledger.total_fees == Decimal("0")


def test_open_position_without_mark_omits_total_pnl() -> None:
    """Open inventory is not marked at entry; missing last-close leaves total PnL unset."""
    buy = LedgerFill(
        side=OrderSide.BUY,
        price=Decimal("100"),
        quantity=Decimal("1"),
        fee=Decimal("0.1"),
        filled_at=_at(1),
    )
    ledger = mark_deployment_ledger(
        starting_cash=Decimal("10000"),
        cash=Decimal("9899.9"),
        fills=(buy,),
        position_quantity=Decimal("1"),
        position_entry_price=Decimal("100"),
        mark_price=None,
    )
    assert ledger.mark_complete is False
    assert ledger.total_net_pnl is None
    assert ledger.unrealized_net_pnl is None
    assert ledger.trade_count == 0
    assert ledger.realized_net_pnl == Decimal("0")


def test_open_position_with_mark_includes_unrealized() -> None:
    """Last-close mark on remaining quantity is added to operational cash."""
    buy = LedgerFill(
        side=OrderSide.BUY,
        price=Decimal("100"),
        quantity=Decimal("1"),
        fee=Decimal("0.1"),
        filled_at=_at(1),
    )
    ledger = mark_deployment_ledger(
        starting_cash=Decimal("10000"),
        cash=Decimal("9899.9"),
        fills=(buy,),
        position_quantity=Decimal("1"),
        position_entry_price=Decimal("100"),
        mark_price=Decimal("105"),
    )
    assert ledger.unrealized_net_pnl == Decimal("5")
    assert ledger.equity == Decimal("10004.9")
    assert ledger.total_net_pnl == Decimal("4.9")
    assert ledger.mark_complete is True


def test_ledger_from_snapshot_joins_fill_sides() -> None:
    """Snapshot fills without a matching order are skipped; matching sides pair."""
    now = _at(0)
    deployment_id = uuid4()
    buy_order_id = uuid4()
    sell_order_id = uuid4()
    orphan_id = uuid4()
    deployment = Deployment(
        id=deployment_id,
        strategy_fingerprint="sha256:" + ("a" * 64),
        strategy_id=UUID(int=1),
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=Decimal("10000"),
        cash=Decimal("10009.79"),
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
    )
    buy_order = Order(
        id=buy_order_id,
        deployment_id=deployment_id,
        intent_id=uuid4(),
        client_order_id="buy",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        status=OrderStatus.FILLED,
        created_at=now,
        updated_at=now,
        price=Decimal("100"),
        filled_quantity=Decimal("1"),
    )
    sell_order = Order(
        id=sell_order_id,
        deployment_id=deployment_id,
        intent_id=uuid4(),
        client_order_id="sell",
        side=OrderSide.SELL,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        status=OrderStatus.FILLED,
        created_at=now,
        updated_at=now,
        price=Decimal("110"),
        filled_quantity=Decimal("1"),
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        orders=(buy_order, sell_order),
        fills=(
            Fill(
                id=uuid4(),
                deployment_id=deployment_id,
                order_id=buy_order_id,
                venue_fill_id="buy",
                price=Decimal("100"),
                quantity=Decimal("1"),
                fee=Decimal("0.1"),
                filled_at=_at(1),
            ),
            Fill(
                id=uuid4(),
                deployment_id=deployment_id,
                order_id=sell_order_id,
                venue_fill_id="sell",
                price=Decimal("110"),
                quantity=Decimal("1"),
                fee=Decimal("0.11"),
                filled_at=_at(2),
            ),
            Fill(
                id=uuid4(),
                deployment_id=deployment_id,
                order_id=orphan_id,
                venue_fill_id="orphan",
                price=Decimal("1"),
                quantity=Decimal("1"),
                fee=Decimal("0"),
                filled_at=_at(3),
            ),
        ),
    )
    ledger = ledger_from_snapshot(snapshot, mark_price=None)
    assert ledger.trade_count == 1
    assert ledger.total_net_pnl == Decimal("9.79")
    assert ledger.total_fees == Decimal("0.21")


def test_snapshot_open_position_uses_stored_entry_and_quantity() -> None:
    """Open position rows win over reconstructed inventory when both exist."""
    now = _at(0)
    deployment_id = uuid4()
    order_id = uuid4()
    deployment = Deployment(
        id=deployment_id,
        strategy_fingerprint="sha256:" + ("a" * 64),
        strategy_id=UUID(int=1),
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=Decimal("10000"),
        cash=Decimal("9899.9"),
        phase=RuntimePhase.OPEN,
        created_at=now,
        updated_at=now,
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        position=Position(
            deployment_id=deployment_id,
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
            stop_price=Decimal("90"),
            target_price=Decimal("120"),
            entered_bar=_at(1),
            updated_at=now,
        ),
        orders=(
            Order(
                id=order_id,
                deployment_id=deployment_id,
                intent_id=uuid4(),
                client_order_id="buy",
                side=OrderSide.BUY,
                kind=OrderKind.POST_ONLY_LIMIT,
                quantity=Decimal("1"),
                status=OrderStatus.FILLED,
                created_at=now,
                updated_at=now,
                price=Decimal("100"),
                filled_quantity=Decimal("1"),
            ),
        ),
        fills=(
            Fill(
                id=uuid4(),
                deployment_id=deployment_id,
                order_id=order_id,
                venue_fill_id="buy",
                price=Decimal("100"),
                quantity=Decimal("1"),
                fee=Decimal("0.1"),
                filled_at=_at(1),
            ),
        ),
    )
    unmarked = ledger_from_snapshot(snapshot, mark_price=None)
    assert unmarked.total_net_pnl is None
    marked = ledger_from_snapshot(snapshot, mark_price=Decimal("105"))
    assert marked.unrealized_net_pnl == Decimal("5")
    assert marked.total_net_pnl == Decimal("4.9")


def test_multi_book_ledger_aggregates_equity_and_marked_exposure() -> None:
    """Two open books use per-product fills and marks; missing one mark fails closed."""
    now = _at(0)
    deployment_id = uuid4()
    deployment = Deployment(
        id=deployment_id,
        strategy_fingerprint="sha256:" + ("b" * 64),
        strategy_id=UUID(int=2),
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=Decimal("10000"),
        cash=Decimal("7000"),
        phase=RuntimePhase.OPEN,
        created_at=now,
        updated_at=now,
    )
    btc_order = uuid4()
    eth_order = uuid4()
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        positions=(
            Position(
                deployment_id=deployment_id,
                product_id="BTC-USD",
                quantity=Decimal("1"),
                entry_price=Decimal("100"),
                stop_price=Decimal("90"),
                target_price=Decimal("120"),
                entered_bar=_at(1),
                updated_at=now,
            ),
            Position(
                deployment_id=deployment_id,
                product_id="ETH-USD",
                quantity=Decimal("1"),
                entry_price=Decimal("50"),
                stop_price=Decimal("40"),
                target_price=Decimal("70"),
                entered_bar=_at(1),
                side=PositionSide.SHORT,
                updated_at=now,
            ),
        ),
        orders=(
            Order(
                id=btc_order,
                deployment_id=deployment_id,
                intent_id=uuid4(),
                client_order_id="btc-buy",
                product_id="BTC-USD",
                side=OrderSide.BUY,
                kind=OrderKind.POST_ONLY_LIMIT,
                quantity=Decimal("1"),
                status=OrderStatus.FILLED,
                created_at=now,
                updated_at=now,
                price=Decimal("100"),
                filled_quantity=Decimal("1"),
            ),
            Order(
                id=eth_order,
                deployment_id=deployment_id,
                intent_id=uuid4(),
                client_order_id="eth-sell",
                product_id="ETH-USD",
                side=OrderSide.SELL,
                kind=OrderKind.POST_ONLY_LIMIT,
                quantity=Decimal("1"),
                status=OrderStatus.FILLED,
                created_at=now,
                updated_at=now,
                price=Decimal("50"),
                filled_quantity=Decimal("1"),
            ),
        ),
        fills=(
            Fill(
                id=uuid4(),
                deployment_id=deployment_id,
                order_id=btc_order,
                venue_fill_id="btc",
                price=Decimal("100"),
                quantity=Decimal("1"),
                fee=Decimal("0"),
                filled_at=_at(1),
            ),
            Fill(
                id=uuid4(),
                deployment_id=deployment_id,
                order_id=eth_order,
                venue_fill_id="eth",
                price=Decimal("50"),
                quantity=Decimal("1"),
                fee=Decimal("0"),
                filled_at=_at(2),
            ),
        ),
    )
    partial = ledger_from_snapshot(
        snapshot,
        marks={"BTC-USD": Decimal("110")},
    )
    assert partial.mark_complete is False
    assert partial.total_net_pnl is None
    assert len(partial.books) == 2
    complete = ledger_from_snapshot(
        snapshot,
        marks={"BTC-USD": Decimal("110"), "ETH-USD": Decimal("45")},
    )
    assert complete.mark_complete is True
    assert complete.marked_exposure == Decimal("65")
    assert complete.equity == Decimal("7065")
    assert complete.total_net_pnl == Decimal("-2935")
