"""Advisory save-time warnings for valid strategy documents (ADR 0090).

Warnings never block a save, a backtest, or a deployment. They flag geometry that is
legal but that plausible volatility can make untradable: a short whose reward-to-risk
take-profit lands at or below zero, or a long whose ATR stop does. The runtime skips
such entries with ``target_not_positive`` / ``stop_not_positive``; the warning says so
before a backtest quietly records few or no trades.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from thytrader.market_data.models import parse_candle_interval
from thytrader.strategies.models import reward_risk_multiple

if TYPE_CHECKING:
    from thytrader.strategies.models import StrategyDefinition

PLAUSIBLE_DAILY_ATR_FRACTION = Decimal("0.20")
"""Stressed ATR as a fraction of price on daily bars (volatile spot alts reach this)."""

_MINUTES_PER_DAY = Decimal(1440)


class StrategyWarningCode(StrEnum):
    """Stable identifiers for advisory strategy warnings."""

    SHORT_TARGET_MAY_BE_NON_POSITIVE = "short_target_may_be_non_positive"
    LONG_STOP_MAY_BE_NON_POSITIVE = "long_stop_may_be_non_positive"


@dataclass(frozen=True, slots=True)
class StrategyWarning:
    """One advisory finding located at the document field it concerns."""

    code: StrategyWarningCode
    loc: tuple[str, ...]
    message: str


def plausible_atr_fraction(timeframe: str) -> Decimal:
    """Return a stressed ATR/price ceiling for one bar clock (daily ceiling x sqrt(days))."""
    minutes = Decimal(parse_candle_interval(timeframe).duration // timedelta(minutes=1))
    scaled = PLAUSIBLE_DAILY_ATR_FRACTION * (minutes / _MINUTES_PER_DAY).sqrt()
    return scaled.quantize(Decimal("0.0001"))


def strategy_warnings(definition: StrategyDefinition) -> tuple[StrategyWarning, ...]:
    """Return advisory warnings for one valid definition (empty when nothing is at risk)."""
    stop = definition.exits.initial_stop
    atr_clock = _atr_timeframe(definition, stop.atr_indicator)
    ceiling = plausible_atr_fraction(atr_clock)
    stop_multiple = Decimal(stop.multiple)
    if definition.entry.side == "short":
        reward = reward_risk_multiple(definition.exits)
        if reward is None:
            return ()
        distance = stop_multiple * reward
        threshold = Decimal(1) / distance
        if threshold > ceiling:
            return ()
        return (
            StrategyWarning(
                code=StrategyWarningCode.SHORT_TARGET_MAY_BE_NON_POSITIVE,
                loc=("exits", "take_profit", "multiple"),
                message=(
                    f"The short take-profit sits {reward} x {stop_multiple} = {distance} ATR "
                    f"below entry, so it is at or below zero whenever {stop.atr_indicator} "
                    f"exceeds {_percent(threshold)} of price; stressed {atr_clock} ATR can "
                    f"reach about {_percent(ceiling)}. Those entries are skipped "
                    "(target_not_positive). Lower take_profit.multiple or "
                    'initial_stop.multiple, or set take_profit to {"kind": "none"}.'
                ),
            ),
        )
    threshold = Decimal(1) / stop_multiple
    if threshold > ceiling:
        return ()
    return (
        StrategyWarning(
            code=StrategyWarningCode.LONG_STOP_MAY_BE_NON_POSITIVE,
            loc=("exits", "initial_stop", "multiple"),
            message=(
                f"The long initial stop sits {stop_multiple} ATR below entry, so it is at or "
                f"below zero whenever {stop.atr_indicator} exceeds {_percent(threshold)} of "
                f"price; stressed {atr_clock} ATR can reach about {_percent(ceiling)}. Those "
                "entries are skipped (stop_not_positive). Lower initial_stop.multiple."
            ),
        ),
    )


def _atr_timeframe(definition: StrategyDefinition, atr_indicator: str) -> str:
    """Return the clock the stop's ATR is computed on (its own timeframe, else the strategy's)."""
    for indicator in definition.indicators:
        if indicator.id == atr_indicator and indicator.timeframe is not None:
            return indicator.timeframe
    return definition.timeframe


def _percent(fraction: Decimal) -> str:
    """Render a fraction as a percentage with two decimals (``0.0333`` -> ``3.33%``)."""
    return f"{(fraction * 100).quantize(Decimal('0.01'))}%"
