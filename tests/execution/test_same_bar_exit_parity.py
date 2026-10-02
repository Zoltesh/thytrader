"""Backtest/paper parity for every same-bar exit combination (ADR 0083, ADR 0093).

One strategy and one candle series run through both the unified backtest kernel and the
paper closed-bar loop. The fixture enters when SMA(2) > 12: the 02:00 signal bar rests a
limit at its close (14), the 03:00 bar fills it, the ATR stop sits at 8 and the 2R
take-profit at 26. Every later bar is shaped so two or more exits are due at once; both
modes must take the same exit, on the same bar, at the same price. The precedence is:
the protective stop (or the ATR trail's pre-trail level), then a touched take-profit,
then the signal exit, then the time exit.

Slippage is zero so the backtest's taker exits sit exactly on their reference price,
which is the price the paper broker fills marketable exits at.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.backtest.test_kernel import _WARMUP, _bars, _hour, _run, _strategy, _with
from thytrader.backtest.kernel import simulate_backtest
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    IntentPurpose,
    RuntimePhase,
)
from thytrader.execution.paper import PaperBroker
from thytrader.market_data.models import MarketProduct
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from thytrader.execution.models import DeploymentSnapshot
    from thytrader.market_data.models import Candle

_SIGNAL_BAR = ("14", "15", "12", "14")
_FILL_BAR = ("14", "15", "12", "12")
_TERMINAL = ("12", "13", "11", "12")
_REASON_PURPOSE: dict[str, IntentPurpose] = {
    "stop_loss": IntentPurpose.STOP,
    "take_profit": IntentPurpose.TAKE_PROFIT,
    "signal": IntentPurpose.SIGNAL_EXIT,
    "time_exit": IntentPurpose.TIME_EXIT,
}
_SMA_BELOW: dict[str, object] = {
    "when": {
        "all": [
            {
                "left": {"indicator": "sma"},
                "operator": "less_than",
                "right": {"literal": "13.5"},
            }
        ]
    }
}


@dataclass(frozen=True, slots=True)
class _Exit:
    """The one exit a mode took: why, on which bar, and at what price."""

    reason: str
    bar: datetime
    price: Decimal


@dataclass(frozen=True, slots=True)
class _Case:
    """One same-bar exit combination and the exit both modes must take."""

    name: str
    bars: tuple[tuple[str, str, str, str], ...]
    expected: str
    take_profit: bool = True
    signal_exit: bool = False
    max_bars_held: int = 96
    trail_multiple: str | None = None


def _product() -> MarketProduct:
    """Fine increments so neither mode rounds the fixture's prices."""
    return MarketProduct(
        product_id="BTC-USD",
        base_currency="BTC",
        quote_currency="USD",
        price_increment=Decimal("0.0001"),
        base_increment=Decimal("0.00000001"),
        quote_increment=Decimal("0.0001"),
        base_min_size=Decimal("0.00000001"),
        quote_min_size=Decimal("0.01"),
        trading_enabled=True,
    )


def _definition(case: _Case) -> StrategyDefinition:
    """The kernel fixture with this case's exit section."""
    exits: dict[str, object] = {
        "initial_stop": {"kind": "atr_multiple", "atr_indicator": "atr", "multiple": "2"},
        "take_profit": (
            {"kind": "reward_risk", "multiple": "2"} if case.take_profit else {"kind": "none"}
        ),
        "trailing_stop": (
            {"enabled": False}
            if case.trail_multiple is None
            else {
                "enabled": True,
                "kind": "atr_multiple",
                "atr_indicator": "atr",
                "multiple": case.trail_multiple,
            }
        ),
        "time_exit": {"max_bars_held": case.max_bars_held},
    }
    if case.signal_exit:
        exits["signal_exit"] = _SMA_BELOW
    return _with(_strategy(), exits=exits)


def _backtest_exit(definition: StrategyDefinition, candles: Sequence[Candle], hours: int) -> _Exit:
    """Run the kernel and return its single trade's exit."""
    result = simulate_backtest(
        _run(definition, evaluation_hours=hours, slippage_bps="0"), definition, candles
    )
    assert len(result.trades) == 1, result.trades
    fill = result.trades[0].exit
    return _Exit(reason=fill.reason, bar=fill.candle_starts_at, price=Decimal(fill.price))


async def _paper_exit(definition: StrategyDefinition, candles: Sequence[Candle]) -> _Exit:
    """Drive one paper book bar by bar over the evaluation bars and return its exit."""
    store = InMemoryExecutionStore()
    now = utc_now()
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint=strategy_fingerprint(definition),
        strategy_id=definition.strategy_id,
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=Decimal("10000"),
        paper_maker_fee_rate=Decimal("0.001"),
        paper_taker_fee_rate=Decimal("0.002"),
        cash=Decimal("10000"),
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
        timeframe="1h",
    )
    await store.create_deployment(deployment)
    snapshot = await store.get_deployment(deployment.id)
    for index in range(len(_WARMUP), len(candles)):
        window = candles[: index + 1]
        snapshot = await process_closed_bar(
            snapshot,
            strategy=definition,
            product=_product(),
            candles=window,
            broker=PaperBroker(),
            store=store,
            allow_new_entries=index == len(_WARMUP),
        )
        taken = _paper_exit_fill(snapshot)
        if taken is not None:
            assert snapshot.position is None
            reason, price = taken
            return _Exit(reason=reason, bar=window[-1].starts_at, price=price)
    pytest.fail("the paper book never exited")


def _paper_exit_fill(snapshot: DeploymentSnapshot) -> tuple[str, Decimal] | None:
    """The backtest reason and fill price of the book's one exit fill, if it has exited."""
    purposes = {
        intent.id: intent.purpose
        for intent in snapshot.intents
        if intent.purpose is not IntentPurpose.ENTRY
    }
    intent_of = {order.id: order.intent_id for order in snapshot.orders}
    fills = [fill for fill in snapshot.fills if intent_of.get(fill.order_id) in purposes]
    if not fills:
        return None
    assert len(fills) == 1, fills
    purpose = purposes[intent_of[fills[0].order_id]]
    reason = next(name for name, value in _REASON_PURPOSE.items() if value is purpose)
    return reason, fills[0].price


_CASES = (
    _Case(
        name="fill_bar_stop_and_target",
        bars=(_SIGNAL_BAR, ("14", "40", "1", "10")),
        expected="stop_loss",
    ),
    _Case(
        name="stop_and_target",
        bars=(_SIGNAL_BAR, _FILL_BAR, ("12", "40", "5", "20")),
        expected="stop_loss",
    ),
    _Case(
        name="stop_and_time",
        bars=(_SIGNAL_BAR, _FILL_BAR, ("12", "13", "5", "12")),
        expected="stop_loss",
        take_profit=False,
        max_bars_held=1,
    ),
    _Case(
        name="stop_and_signal",
        bars=(_SIGNAL_BAR, _FILL_BAR, ("12", "13", "5", "12")),
        expected="stop_loss",
        take_profit=False,
        signal_exit=True,
    ),
    _Case(
        name="stop_signal_and_time",
        bars=(_SIGNAL_BAR, _FILL_BAR, ("12", "13", "5", "12")),
        expected="stop_loss",
        take_profit=False,
        signal_exit=True,
        max_bars_held=1,
    ),
    _Case(
        name="stop_target_signal_and_time",
        bars=(_SIGNAL_BAR, _FILL_BAR, ("12", "40", "5", "12")),
        expected="stop_loss",
        signal_exit=True,
        max_bars_held=1,
    ),
    _Case(
        name="gapped_stop_and_time",
        bars=(_SIGNAL_BAR, _FILL_BAR, ("6", "7", "5", "6")),
        expected="stop_loss",
        take_profit=False,
        max_bars_held=1,
    ),
    _Case(
        name="target_and_signal",
        bars=(_SIGNAL_BAR, _FILL_BAR, ("12", "40", "11", "12")),
        expected="take_profit",
        signal_exit=True,
    ),
    _Case(
        name="target_and_time",
        bars=(_SIGNAL_BAR, _FILL_BAR, ("12", "40", "11", "12")),
        expected="take_profit",
        max_bars_held=1,
    ),
    _Case(
        name="target_signal_and_time",
        bars=(_SIGNAL_BAR, _FILL_BAR, ("12", "40", "11", "12")),
        expected="take_profit",
        signal_exit=True,
        max_bars_held=1,
    ),
    _Case(
        name="signal_and_time",
        bars=(_SIGNAL_BAR, _FILL_BAR, ("12", "13", "11", "12")),
        expected="signal",
        take_profit=False,
        signal_exit=True,
        max_bars_held=1,
    ),
    _Case(
        name="time_only",
        bars=(_SIGNAL_BAR, _FILL_BAR, ("12", "13", "11", "12")),
        expected="time_exit",
        take_profit=False,
        max_bars_held=1,
    ),
    _Case(
        name="trailed_stop_and_time",
        bars=(_SIGNAL_BAR, _FILL_BAR, ("12", "24", "11.5", "20"), ("20", "21", "11", "18")),
        expected="stop_loss",
        take_profit=False,
        max_bars_held=2,
        trail_multiple="1",
    ),
    _Case(
        name="trailed_stop_and_target",
        bars=(_SIGNAL_BAR, _FILL_BAR, ("12", "24", "11.5", "20"), ("20", "40", "11", "18")),
        expected="stop_loss",
        trail_multiple="1",
    ),
    _Case(
        name="trailed_stop_and_signal",
        bars=(_SIGNAL_BAR, _FILL_BAR, ("12", "24", "11.5", "20"), ("20", "21", "1", "2")),
        expected="stop_loss",
        take_profit=False,
        signal_exit=True,
        trail_multiple="1",
    ),
    _Case(
        name="trail_ratchets_on_the_time_exit_bar",
        bars=(_SIGNAL_BAR, _FILL_BAR, ("12", "24", "11.5", "20")),
        expected="time_exit",
        take_profit=False,
        max_bars_held=1,
        trail_multiple="1",
    ),
)


@pytest.mark.anyio
@pytest.mark.parametrize("case", _CASES, ids=[case.name for case in _CASES])
async def test_backtest_and_paper_take_the_same_exit_on_a_same_bar_tie(case: _Case) -> None:
    """Both modes take the precedence winner on the same bar at the same price."""
    definition = _definition(case)
    candles = _bars(*_WARMUP, *case.bars, _TERMINAL)
    backtest = _backtest_exit(definition, candles, len(case.bars))
    paper = await _paper_exit(definition, candles[:-1])
    assert backtest.reason == case.expected
    assert paper == backtest
    assert backtest.bar == _hour(len(_WARMUP) + len(case.bars) - 1)
