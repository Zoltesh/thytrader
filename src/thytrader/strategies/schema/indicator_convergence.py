"""Warmup after which an indicator's seed has decayed to a negligible weight (ADR 0140).

The minimum warmup (``indicator_warmup``) is the bar of an indicator's first value. Recursive
indicators are seeded at that bar (an SMA of their first inputs, or a source value for KAMA)
and only then decay toward their established value, so two series seeded at different
instants disagree until the seed's remaining weight is small. This module adds, per indicator,
the bars needed for each recursive stage's seed weight to fall to ``RESIDUAL_SEED_WEIGHT``.

For a stage with smoothing ``alpha``, the remaining seed weight after ``k`` updates is
``(1 - alpha) ** k``, so ``k = ceil(log(eps) / log(1 - alpha))``. Chained stages add their
updates after the minimum warmup, which bounds the composite conservatively. This caps the
seed's weight; it does not promise identical signals at an exact threshold or crossover.

Some kinds keep state that no warmup length removes (Supertrend direction, Parabolic SAR
trend, cumulative OBV and A/D levels). They are ``anchor_sensitive``: convergence warmup
covers their recursive inputs only.
"""

from __future__ import annotations

from math import ceil, log
from typing import TYPE_CHECKING

from thytrader.strategies.schema.indicator_definition import indicator_offset
from thytrader.strategies.schema.indicator_parameters import (
    IndicatorKind,
    IndicatorParameters,
    KamaIndicatorParameters,
    KeltnerIndicatorParameters,
    MacdIndicatorParameters,
    StochasticRsiIndicatorParameters,
    SupertrendIndicatorParameters,
    TsiIndicatorParameters,
)
from thytrader.strategies.schema.indicator_warmup import (
    _base_min_warmup,
    _parameters_as,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from thytrader.strategies.schema.indicator_definition import IndicatorDefinition
    from thytrader.strategies.schema.indicator_parameters import IndicatorParameterBlock

RESIDUAL_SEED_WEIGHT = 1e-3
"""Largest remaining weight of an indicator's seed once its convergence warmup has passed."""

ANCHOR_SENSITIVE_KINDS = frozenset(
    {
        IndicatorKind.SUPERTREND,
        IndicatorKind.PARABOLIC_SAR,
        IndicatorKind.OBV,
        IndicatorKind.ACCUMULATION_DISTRIBUTION,
    }
)
"""Kinds whose state (trend direction, cumulative level) depends on where the series starts.

OBV and A/D levels shift by a constant with the start; comparisons with their own signal line
or same-series differences cancel that shift, so their crossovers are not affected."""


def updates_to_converge(alpha: float) -> int:
    """Updates after a seed until its weight is at most ``RESIDUAL_SEED_WEIGHT``."""
    if alpha >= 1:
        return 0
    return ceil(log(RESIDUAL_SEED_WEIGHT) / log(1 - alpha))


def _ema_updates(period: int) -> int:
    """Updates for an SMA-seeded EMA with ``alpha = 2 / (period + 1)``."""
    return updates_to_converge(2 / (period + 1))


def _wilder_updates(period: int) -> int:
    """Updates for an SMA-seeded Wilder average with ``alpha = 1 / period``."""
    return updates_to_converge(1 / period)


def indicator_convergence_warmup(indicator: IndicatorDefinition) -> int:
    """Closed bars before one indicator's seeds have decayed, including its bar lag."""
    return _base_convergence_warmup(indicator) + indicator_offset(indicator)


def indicator_anchor_sensitive(indicator: IndicatorDefinition) -> bool:
    """Whether some state of this indicator depends on its first bar for every warmup."""
    return indicator.kind in ANCHOR_SENSITIVE_KINDS


def _base_convergence_warmup(indicator: IndicatorDefinition) -> int:
    """Minimum warmup plus the decay updates of every recursive stage of the kind."""
    if indicator.kind is IndicatorKind.KELTNER:
        return _keltner_total(indicator.parameters)
    minimum = _base_min_warmup(indicator)
    rule = _CONVERGENCE_RULES.get(indicator.kind)
    if rule is None:
        return minimum
    return minimum + rule(indicator.parameters)


def _period(parameters: IndicatorParameterBlock) -> int:
    return _parameters_as(parameters, IndicatorParameters).period


def _ema(parameters: IndicatorParameterBlock) -> int:
    return _ema_updates(_period(parameters))


def _chained_ema(stages: int) -> Callable[[IndicatorParameterBlock], int]:
    def rule(parameters: IndicatorParameterBlock) -> int:
        return stages * _ema_updates(_period(parameters))

    return rule


def _wilder(parameters: IndicatorParameterBlock) -> int:
    return _wilder_updates(_period(parameters))


def _adx(parameters: IndicatorParameterBlock) -> int:
    """Wilder TR/DM smoothing, then Wilder smoothing of DX: two serial stages."""
    return 2 * _wilder_updates(_period(parameters))


def _macd(parameters: IndicatorParameterBlock) -> int:
    """Fast and slow EMAs run in parallel (the slow one decays last), then the signal EMA."""
    shaped = _parameters_as(parameters, MacdIndicatorParameters)
    return _ema_updates(shaped.slow_period) + _ema_updates(shaped.signal_period)


def _tsi(parameters: IndicatorParameterBlock) -> int:
    """Double-smoothed changes (long then short EMA), then the signal EMA."""
    shaped = _parameters_as(parameters, TsiIndicatorParameters)
    return (
        _ema_updates(shaped.long_period)
        + _ema_updates(shaped.short_period)
        + _ema_updates(shaped.signal_period)
    )


def _kama(parameters: IndicatorParameterBlock) -> int:
    """KAMA's slowest coefficient is the squared slow constant ``(2 / (slow + 1)) ** 2``."""
    shaped = _parameters_as(parameters, KamaIndicatorParameters)
    return max(1, updates_to_converge((2 / (shaped.slow_period + 1)) ** 2))


def _supertrend(parameters: IndicatorParameterBlock) -> int:
    """The ATR input decays like Wilder; the band direction stays anchor-sensitive."""
    return _wilder_updates(_parameters_as(parameters, SupertrendIndicatorParameters).period)


def _stochastic_rsi(parameters: IndicatorParameterBlock) -> int:
    """Only the RSI stage is recursive; the stochastic window and %K/%D SMAs are exact."""
    return _wilder_updates(_parameters_as(parameters, StochasticRsiIndicatorParameters).rsi_period)


def _keltner_total(parameters: IndicatorParameterBlock) -> int:
    """EMA middle and Wilder ATR run in parallel: the later-converging branch decides."""
    shaped = _parameters_as(parameters, KeltnerIndicatorParameters)
    return max(
        shaped.period + _ema_updates(shaped.period),
        shaped.atr_period + _wilder_updates(shaped.atr_period),
    )


_CONVERGENCE_RULES: dict[IndicatorKind, Callable[[IndicatorParameterBlock], int]] = {
    IndicatorKind.EMA: _ema,
    IndicatorKind.DEMA: _chained_ema(2),
    IndicatorKind.TEMA: _chained_ema(3),
    IndicatorKind.TRIX: _chained_ema(3),
    IndicatorKind.FORCE_INDEX: _ema,
    IndicatorKind.MACD: _macd,
    IndicatorKind.PPO: _macd,
    IndicatorKind.TSI: _tsi,
    IndicatorKind.KAMA: _kama,
    IndicatorKind.RSI: _wilder,
    IndicatorKind.ATR: _wilder,
    IndicatorKind.NATR: _wilder,
    IndicatorKind.ADX: _adx,
    IndicatorKind.SUPERTREND: _supertrend,
    IndicatorKind.STOCHASTIC_RSI: _stochastic_rsi,
}
"""Extra decay updates per recursive kind, added after the kind's minimum warmup."""

RECURSIVE_KINDS = frozenset({*_CONVERGENCE_RULES, IndicatorKind.KELTNER})
"""Kinds with at least one seeded recursive stage; every other kind is an exact window."""


def indicator_recursive(kind: IndicatorKind) -> bool:
    """Whether a kind needs more than its minimum warmup to converge."""
    return kind in RECURSIVE_KINDS
