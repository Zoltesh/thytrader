"""Explicit entry skip reasons shared by backtest, paper, and live (ADR 0090)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from thytrader.backtest.kernel import _size_entry
from thytrader.evaluation.trace import (
    EntryConditionOutcome,
    IndicatorTraceValue,
    SignalTraceRecord,
)
from thytrader.execution.geometry import (
    EntryLevels,
    EntrySkipReason,
    entry_levels,
    entry_skip_category,
    entry_skip_detail,
    protective_stop_limit_price,
)
from thytrader.execution.models import OrderSide, PositionSide
from thytrader.execution.sizing import SizedEntry, size_entry_or_skip, size_pyramid_add_or_skip
from thytrader.market_data.models import MarketProduct
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import StrategyDefinition


def _product(price_increment: str = "0.01") -> MarketProduct:
    """AVAX-like venue increments."""
    return MarketProduct(
        product_id="AVAX-USDC",
        base_currency="AVAX",
        quote_currency="USDC",
        price_increment=Decimal(price_increment),
        base_increment=Decimal("0.00000001"),
        quote_increment=Decimal("0.01"),
        base_min_size=Decimal("0.0001"),
        quote_min_size=Decimal("1"),
        trading_enabled=True,
    )


def _strategy(
    *, side: str = "short", stop: str = "3", take_profit: dict[str, str] | None = None
) -> StrategyDefinition:
    """Template strategy with the side, ATR stop multiple, and take-profit under test."""
    payload = create_template_strategy(now=datetime(2026, 1, 1, tzinfo=UTC)).model_dump(
        mode="python"
    )
    payload["entry"]["side"] = side
    payload["exits"]["initial_stop"]["multiple"] = stop
    payload["exits"]["take_profit"] = take_profit or {"kind": "reward_risk", "multiple": "10"}
    payload["sizing"]["min_quote_notional"] = "1"
    return StrategyDefinition.model_validate(payload)


def _signal(strategy: StrategyDefinition, atr: str) -> SignalTraceRecord:
    """One matched trace record carrying every indicator, with the stop ATR set."""
    atr_id = strategy.exits.initial_stop.atr_indicator
    return SignalTraceRecord(
        candle_starts_at=datetime(2026, 1, 2, tzinfo=UTC),
        indicator_values=tuple(
            IndicatorTraceValue(
                indicator_id=indicator.id, value=atr if indicator.id == atr_id else "1"
            )
            for indicator in strategy.indicators
        ),
        entry_condition=EntryConditionOutcome.MATCHED,
    )


def test_short_target_at_or_below_zero_is_named_not_silent() -> None:
    """Reward x stop distance at or above price yields ``target_not_positive``."""
    assert (
        entry_levels(
            side=PositionSide.SHORT,
            entry_price=Decimal("30"),
            stop_distance=Decimal("3"),
            reward_multiple=Decimal("10"),
        )
        is EntrySkipReason.TARGET_NOT_POSITIVE
    )
    legal = entry_levels(
        side=PositionSide.SHORT,
        entry_price=Decimal("30"),
        stop_distance=Decimal("3"),
        reward_multiple=Decimal("3"),
    )
    assert legal == EntryLevels(stop_price=Decimal("33"), target_price=Decimal("21"))


def test_take_profit_none_checks_only_the_stop() -> None:
    """Without a take-profit a wide short stop is legal and the target is None."""
    levels = entry_levels(
        side=PositionSide.SHORT,
        entry_price=Decimal("30"),
        stop_distance=Decimal("9"),
        reward_multiple=None,
    )
    assert levels == EntryLevels(stop_price=Decimal("39"), target_price=None)
    assert (
        entry_levels(
            side=PositionSide.LONG,
            entry_price=Decimal("30"),
            stop_distance=Decimal("30"),
            reward_multiple=None,
        )
        is EntrySkipReason.STOP_NOT_POSITIVE
    )


@pytest.mark.parametrize(
    ("side", "entry", "distance", "reward", "increment", "expected"),
    [
        (PositionSide.LONG, "0", "1", "2", None, EntrySkipReason.ENTRY_PRICE_NOT_POSITIVE),
        (PositionSide.LONG, "10", "0", "2", None, EntrySkipReason.STOP_DISTANCE_NOT_POSITIVE),
        (
            PositionSide.SHORT,
            "10",
            "0.004",
            None,
            "0.01",
            EntrySkipReason.STOP_WITHIN_PRICE_INCREMENT,
        ),
        (
            PositionSide.LONG,
            "10",
            "0.002",
            "1",
            "0.01",
            EntrySkipReason.TARGET_WITHIN_PRICE_INCREMENT,
        ),
    ],
)
def test_every_illegal_geometry_has_one_precise_reason(
    side: PositionSide,
    entry: str,
    distance: str,
    reward: str | None,
    increment: str | None,
    expected: EntrySkipReason,
) -> None:
    """Price, distance, and increment failures each name their own code."""
    result = entry_levels(
        side=side,
        entry_price=Decimal(entry),
        stop_distance=Decimal(distance),
        reward_multiple=None if reward is None else Decimal(reward),
        price_increment=None if increment is None else Decimal(increment),
    )
    assert result is expected


def test_skip_reasons_have_categories_and_operator_details() -> None:
    """Geometry versus sizing categories drive the decision journal's skip_reason."""
    assert entry_skip_category(EntrySkipReason.TARGET_NOT_POSITIVE) == "geometry"
    assert entry_skip_category(EntrySkipReason.NOTIONAL_BELOW_MINIMUM) == "sizing"
    for reason in EntrySkipReason:
        assert entry_skip_detail(reason)


def test_live_sizing_names_the_skip_and_parities_the_backtest_kernel() -> None:
    """The paper/live sizer and the backtest kernel refuse the same short for the same reason."""
    strategy = _strategy()
    live = size_entry_or_skip(
        strategy=strategy,
        cash=Decimal("10000"),
        entry_price=Decimal("30"),
        atr=Decimal("1.5"),
        product=_product(),
        side=PositionSide.SHORT,
    )
    research = _size_entry(
        _signal(strategy, "1.5"),
        strategy=strategy,
        cash=Decimal("10000"),
        limit_price=Decimal("30"),
        maker_fee_rate=Decimal("0"),
    )
    assert live is EntrySkipReason.TARGET_NOT_POSITIVE
    assert research is EntrySkipReason.TARGET_NOT_POSITIVE


def test_take_profit_none_sizes_identically_in_live_and_backtest() -> None:
    """With no TP both sizers rest the same stop, no target, and the same risk size."""
    strategy = _strategy(take_profit={"kind": "none"})
    live = size_entry_or_skip(
        strategy=strategy,
        cash=Decimal("10000"),
        entry_price=Decimal("30"),
        atr=Decimal("1.5"),
        product=_product("0.0001"),
        side=PositionSide.SHORT,
    )
    research = _size_entry(
        _signal(strategy, "1.5"),
        strategy=strategy,
        cash=Decimal("10000"),
        limit_price=Decimal("30"),
        maker_fee_rate=Decimal("0"),
    )
    assert isinstance(live, SizedEntry)
    assert not isinstance(research, EntrySkipReason)
    assert live.target_price is None
    assert research.target_price is None
    assert live.stop_price == research.stop_price == Decimal("34.5")
    assert live.quantity == research.quantity.quantize(Decimal("0.00000001"))


@pytest.mark.parametrize(
    ("cash", "minimum", "expected"),
    [
        ("0", "1", EntrySkipReason.INSUFFICIENT_CASH),
        ("10000", "5000", EntrySkipReason.NOTIONAL_BELOW_MINIMUM),
    ],
)
def test_sizing_refusals_are_named(cash: str, minimum: str, expected: EntrySkipReason) -> None:
    """Cash and strategy-minimum refusals name themselves instead of returning None."""
    payload = _strategy(take_profit={"kind": "none"}).model_dump(mode="python")
    payload["sizing"]["min_quote_notional"] = minimum
    payload["sizing"]["max_quote_notional"] = "100000"
    strategy = StrategyDefinition.model_validate(payload)
    sized = size_entry_or_skip(
        strategy=strategy,
        cash=Decimal(cash),
        entry_price=Decimal("30"),
        atr=Decimal("1.5"),
        product=_product(),
        side=PositionSide.SHORT,
    )
    assert sized is expected


def test_venue_minimum_refusals_are_named() -> None:
    """A quantity under the product minimum is ``quantity_below_venue_minimum``."""
    strategy = _strategy(side="long", take_profit={"kind": "none"})
    product = replace(_product(), base_min_size=Decimal("1000"))
    sized = size_entry_or_skip(
        strategy=strategy,
        cash=Decimal("10000"),
        entry_price=Decimal("30"),
        atr=Decimal("1.5"),
        product=product,
    )
    assert sized is EntrySkipReason.QUANTITY_BELOW_VENUE_MINIMUM


def test_pyramid_add_past_the_stop_is_named() -> None:
    """An add priced at or beyond the existing stop has no risk distance."""
    sized = size_pyramid_add_or_skip(
        strategy=_strategy(side="long", take_profit={"kind": "none"}),
        cash=Decimal("10000"),
        entry_price=Decimal("30"),
        existing_stop=Decimal("31"),
        existing_target=None,
        product=_product(),
    )
    assert sized is EntrySkipReason.STOP_DISTANCE_NOT_POSITIVE


def test_protective_stop_limit_sits_five_percent_through_the_stop() -> None:
    """Sell stops limit below (rounded down); buy-to-cover stops limit above (rounded up)."""
    assert protective_stop_limit_price(
        cover_side=OrderSide.SELL, stop_price=Decimal("90"), price_increment=Decimal("0.01")
    ) == Decimal("85.50")
    assert protective_stop_limit_price(
        cover_side=OrderSide.BUY, stop_price=Decimal("33.33"), price_increment=Decimal("0.01")
    ) == Decimal("35.00")
    assert protective_stop_limit_price(
        cover_side=OrderSide.SELL, stop_price=Decimal("1.07"), price_increment=Decimal("0.1")
    ) == Decimal("1.0")
