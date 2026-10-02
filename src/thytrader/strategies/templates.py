"""Named conservative draft templates for research authoring."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from thytrader.strategies.models import (
    AllCondition,
    AnyCondition,
    AtrMultipleStop,
    BollingerIndicatorParameters,
    ComparisonCondition,
    ComparisonOperator,
    ConstantIndicatorParameters,
    DataRequirements,
    DisabledTrailingStop,
    EmptyIndicatorParameters,
    EntryDefinition,
    ExecutionPreferences,
    ExitDefinition,
    IndicatorDefinition,
    IndicatorKind,
    IndicatorOperand,
    IndicatorParameters,
    KeltnerIndicatorParameters,
    LiteralOperand,
    MacdIndicatorParameters,
    PortfolioLimits,
    RewardRiskTakeProfit,
    RiskFractionSizing,
    StrategyDefinition,
    StrategyMetadata,
    SupertrendIndicatorParameters,
    TimeExit,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from thytrader.market_data.models import DatasetTimeframe
    from thytrader.strategies.models import Instrument

_OHLCV: tuple[
    Literal["open"], Literal["high"], Literal["low"], Literal["close"], Literal["volume"]
] = (
    "open",
    "high",
    "low",
    "close",
    "volume",
)


class StrategyTemplateId(StrEnum):
    """Fail-closed draft templates. ema-trend is the historical reference."""

    EMA_TREND = "ema-trend"
    RSI_MEAN_REVERSION = "rsi-mean-reversion"
    MACD_TREND = "macd-trend"
    BOLLINGER_MEAN_REVERSION = "bollinger-mean-reversion"
    DONCHIAN_BREAKOUT = "donchian-breakout"
    SUPERTREND_TREND = "supertrend-trend"
    SQUEEZE_BREAKOUT = "squeeze-breakout"
    ZSCORE_MEAN_REVERSION = "zscore-mean-reversion"


def template_catalog() -> tuple[dict[str, str], ...]:
    """Return agent-visible template identities and one-line descriptions."""
    return (
        {
            "id": StrategyTemplateId.EMA_TREND.value,
            "name": "EMA trend",
            "description": (
                "Fast EMA crosses above slow EMA with RSI filter (conservative reference)."
            ),
        },
        {
            "id": StrategyTemplateId.RSI_MEAN_REVERSION.value,
            "name": "RSI mean reversion",
            "description": "Long when RSI is at or below 30; ATR stop and reward/risk target.",
        },
        {
            "id": StrategyTemplateId.MACD_TREND.value,
            "name": "MACD trend",
            "description": "MACD line crosses above signal; ATR stop and reward/risk target.",
        },
        {
            "id": StrategyTemplateId.BOLLINGER_MEAN_REVERSION.value,
            "name": "Bollinger mean reversion",
            "description": "Long when close is at or below the lower band; ATR stop and target.",
        },
        {
            "id": StrategyTemplateId.DONCHIAN_BREAKOUT.value,
            "name": "Donchian breakout",
            "description": (
                "Long when close crosses above the prior 20-bar Donchian high (offset 1); "
                "ATR stop and reward/risk target."
            ),
        },
        {
            "id": StrategyTemplateId.SUPERTREND_TREND.value,
            "name": "Supertrend trend",
            "description": (
                "Long when Supertrend(10, 3) flips to an uptrend and ADX(14) is at least 20; "
                "ATR stop and reward/risk target."
            ),
        },
        {
            "id": StrategyTemplateId.SQUEEZE_BREAKOUT.value,
            "name": "Squeeze breakout",
            "description": (
                "Long when close crosses above the upper Bollinger band right after the bands "
                "sat inside the Keltner channel (prior-bar squeeze); ATR stop and target."
            ),
        },
        {
            "id": StrategyTemplateId.ZSCORE_MEAN_REVERSION.value,
            "name": "Z-score mean reversion",
            "description": (
                "Long when the 20-bar close z-score is at or below -2 in a ranging regime "
                "(ADX below 20 or Choppiness above 61.8); ATR stop and target."
            ),
        },
    )


def parse_template_id(value: str) -> StrategyTemplateId:
    """Reject unknown template ids before constructing a draft."""
    try:
        return StrategyTemplateId(value)
    except ValueError:
        allowed = ", ".join(item.value for item in StrategyTemplateId)
        message = f"Unknown strategy template. Use one of: {allowed}."
        raise ValueError(message) from None


def template_blueprint(template_id: StrategyTemplateId) -> dict[str, Any]:
    """Return one template's indicator ids, defaults, and sweepable axes.

    This is discovery metadata for agents authoring sweeps: it names the
    indicator ids a ``parameter_axes`` entry may reference and the exit/sizing
    defaults the template ships, without granting any trading authority.
    """
    blueprints: dict[StrategyTemplateId, dict[str, Any]] = {
        StrategyTemplateId.EMA_TREND: {
            "warmup_bars": 50,
            "indicator_ids": ("fast", "slow", "rsi", "atr"),
            "defaults": {
                "fast.period": "20",
                "slow.period": "50",
                "rsi.period": "14",
                "atr.period": "14",
                "entry.literal.rsi_gte": "50",
                "sizing.risk_fraction": "0.005",
                "exits.initial_stop_multiple": "2",
                "exits.take_profit_multiple": "2",
                "exits.max_bars_held": "96",
            },
            "sweepable_axes": (
                {"indicator_id": "fast", "parameter": "period", "range": [2, 500]},
                {"indicator_id": "slow", "parameter": "period", "range": [2, 500]},
                {"indicator_id": "rsi", "parameter": "period", "range": [2, 100]},
                {"target": "exits", "parameter": "initial_stop_multiple"},
                {"target": "exits", "parameter": "take_profit_multiple"},
                {"target": "exits", "parameter": "max_bars_held"},
                {"target": "sizing", "parameter": "risk_fraction"},
                {"target": "entry_literal", "indicator_id": "rsi", "parameter": "literal"},
            ),
        },
        StrategyTemplateId.RSI_MEAN_REVERSION: {
            "warmup_bars": 15,
            "indicator_ids": ("rsi", "atr"),
            "defaults": {
                "rsi.period": "14",
                "entry.literal.rsi_lte": "30",
                "sizing.risk_fraction": "0.005",
                "exits.initial_stop_multiple": "2",
                "exits.take_profit_multiple": "2",
                "exits.max_bars_held": "96",
            },
            "sweepable_axes": (
                {"indicator_id": "rsi", "parameter": "period", "range": [2, 100]},
                {"target": "exits", "parameter": "initial_stop_multiple"},
                {"target": "exits", "parameter": "take_profit_multiple"},
                {"target": "exits", "parameter": "max_bars_held"},
                {"target": "sizing", "parameter": "risk_fraction"},
                {"target": "entry_literal", "indicator_id": "rsi", "parameter": "literal"},
            ),
        },
        StrategyTemplateId.MACD_TREND: {
            "warmup_bars": 34,
            "indicator_ids": ("macd", "atr"),
            "defaults": {
                "macd.fast_period": "12",
                "macd.slow_period": "26",
                "macd.signal_period": "9",
                "atr.period": "14",
                "sizing.risk_fraction": "0.005",
                "exits.initial_stop_multiple": "2",
                "exits.take_profit_multiple": "2",
                "exits.max_bars_held": "96",
            },
            "sweepable_axes": (
                {"indicator_id": "macd", "parameter": "fast_period", "range": [2, 500]},
                {"indicator_id": "macd", "parameter": "slow_period", "range": [2, 500]},
                {"indicator_id": "macd", "parameter": "signal_period", "range": [2, 500]},
                {"target": "exits", "parameter": "initial_stop_multiple"},
                {"target": "exits", "parameter": "take_profit_multiple"},
                {"target": "exits", "parameter": "max_bars_held"},
                {"target": "sizing", "parameter": "risk_fraction"},
            ),
        },
        StrategyTemplateId.BOLLINGER_MEAN_REVERSION: {
            "warmup_bars": 20,
            "indicator_ids": ("close", "bands", "atr"),
            "defaults": {
                "bands.period": "20",
                "bands.stdev_multiplier": "2",
                "atr.period": "14",
                "sizing.risk_fraction": "0.005",
                "exits.initial_stop_multiple": "2",
                "exits.take_profit_multiple": "2",
                "exits.max_bars_held": "96",
            },
            "sweepable_axes": (
                {"indicator_id": "bands", "parameter": "period", "range": [2, 500]},
                {"indicator_id": "bands", "parameter": "stdev_multiplier"},
                {"target": "exits", "parameter": "initial_stop_multiple"},
                {"target": "exits", "parameter": "take_profit_multiple"},
                {"target": "exits", "parameter": "max_bars_held"},
                {"target": "sizing", "parameter": "risk_fraction"},
            ),
        },
        **_catalog_template_blueprints(),
    }
    blueprint = dict(blueprints[template_id])
    blueprint["id"] = template_id.value
    return blueprint


_SHARED_DEFAULTS: dict[str, str] = {
    "atr.period": "14",
    "sizing.risk_fraction": "0.005",
    "exits.initial_stop_multiple": "2",
    "exits.take_profit_multiple": "2",
    "exits.max_bars_held": "96",
}
_SHARED_AXES: tuple[dict[str, object], ...] = (
    {"target": "exits", "parameter": "initial_stop_multiple"},
    {"target": "exits", "parameter": "take_profit_multiple"},
    {"target": "exits", "parameter": "max_bars_held"},
    {"target": "sizing", "parameter": "risk_fraction"},
)


def _catalog_template_blueprints() -> dict[StrategyTemplateId, dict[str, Any]]:
    """Return blueprints for the templates built on the wider indicator catalog."""
    return {
        StrategyTemplateId.DONCHIAN_BREAKOUT: {
            "warmup_bars": 21,
            "indicator_ids": ("close", "channel", "atr"),
            "defaults": {"channel.period": "20", "channel.offset": "1", **_SHARED_DEFAULTS},
            "sweepable_axes": (
                {"indicator_id": "channel", "parameter": "period", "range": [2, 500]},
                {"indicator_id": "channel", "parameter": "offset", "range": [1, 500]},
                *_SHARED_AXES,
            ),
        },
        StrategyTemplateId.SUPERTREND_TREND: {
            "warmup_bars": 27,
            "indicator_ids": ("trend", "zero", "adx", "atr"),
            "defaults": {
                "trend.period": "10",
                "trend.multiplier": "3",
                "adx.period": "14",
                "entry.literal.adx_gte": "20",
                **_SHARED_DEFAULTS,
            },
            "sweepable_axes": (
                {"indicator_id": "trend", "parameter": "period", "range": [2, 100]},
                {"indicator_id": "trend", "parameter": "multiplier"},
                {"target": "entry_literal", "indicator_id": "adx", "parameter": "literal"},
                *_SHARED_AXES,
            ),
        },
        StrategyTemplateId.SQUEEZE_BREAKOUT: {
            "warmup_bars": 21,
            "indicator_ids": (
                "close",
                "bands",
                "channel",
                "prior_bands",
                "prior_channel",
                "atr",
            ),
            "defaults": {
                "bands.period": "20",
                "bands.stdev_multiplier": "2",
                "channel.period": "20",
                "channel.atr_period": "10",
                "channel.multiplier": "1.5",
                "prior_bands.offset": "1",
                "prior_channel.offset": "1",
                **_SHARED_DEFAULTS,
            },
            # The prior_* declarations must keep the same parameters as bands/channel, so
            # only exits and sizing are advertised as independent axes.
            "sweepable_axes": _SHARED_AXES,
        },
        StrategyTemplateId.ZSCORE_MEAN_REVERSION: {
            "warmup_bars": 27,
            "indicator_ids": ("z", "adx", "chop", "atr"),
            "defaults": {
                "z.period": "20",
                "adx.period": "14",
                "chop.period": "14",
                "entry.literal.z_lte": "-2",
                "entry.literal.adx_lt": "20",
                "entry.literal.chop_gt": "61.8",
                **_SHARED_DEFAULTS,
            },
            "sweepable_axes": (
                {"indicator_id": "z", "parameter": "period", "range": [2, 500]},
                {"target": "entry_literal", "indicator_id": "z", "parameter": "literal"},
                {"target": "entry_literal", "indicator_id": "adx", "parameter": "literal"},
                {"target": "entry_literal", "indicator_id": "chop", "parameter": "literal"},
                *_SHARED_AXES,
            ),
        },
    }


def build_template_definition(
    *,
    template_id: StrategyTemplateId,
    strategy_id: UUID,
    created_at: datetime,
    instrument: Instrument,
    timeframe: DatasetTimeframe,
) -> StrategyDefinition:
    """Return one validated draft for the selected template."""
    return _TEMPLATE_BUILDERS[template_id](strategy_id, created_at, instrument, timeframe)


def _ema_trend(
    strategy_id: UUID,
    created_at: datetime,
    instrument: Instrument,
    timeframe: DatasetTimeframe,
) -> StrategyDefinition:
    """Historical conservative EMA crossover reference."""
    return _draft(
        strategy_id=strategy_id,
        created_at=created_at,
        instrument=instrument,
        timeframe=timeframe,
        name=_template_name(instrument.product_id, timeframe, "EMA trend"),
        description="Reference research strategy; not trading authority.",
        warmup_bars=50,
        indicators=(
            IndicatorDefinition(
                id="fast",
                kind=IndicatorKind.EMA,
                input="close",
                parameters=IndicatorParameters(period=20),
            ),
            IndicatorDefinition(
                id="slow",
                kind=IndicatorKind.EMA,
                input="close",
                parameters=IndicatorParameters(period=50),
            ),
            IndicatorDefinition(
                id="rsi",
                kind=IndicatorKind.RSI,
                input="close",
                parameters=IndicatorParameters(period=14),
            ),
            _atr(),
        ),
        when=AllCondition(
            all=(
                ComparisonCondition(
                    left=IndicatorOperand(indicator="fast"),
                    operator=ComparisonOperator.CROSSES_ABOVE,
                    right=IndicatorOperand(indicator="slow"),
                ),
                ComparisonCondition(
                    left=IndicatorOperand(indicator="rsi"),
                    operator=ComparisonOperator.GTE,
                    right=LiteralOperand(literal="50"),
                ),
            )
        ),
        tags=("reference",),
    )


def _rsi_mean_reversion(
    strategy_id: UUID,
    created_at: datetime,
    instrument: Instrument,
    timeframe: DatasetTimeframe,
) -> StrategyDefinition:
    """Long when RSI is oversold. Warmup covers RSI lookback plus ATR."""
    return _draft(
        strategy_id=strategy_id,
        created_at=created_at,
        instrument=instrument,
        timeframe=timeframe,
        name=_template_name(instrument.product_id, timeframe, "RSI mean reversion"),
        description="RSI mean-reversion research template; not trading authority.",
        warmup_bars=15,
        indicators=(
            IndicatorDefinition(
                id="rsi",
                kind=IndicatorKind.RSI,
                input="close",
                parameters=IndicatorParameters(period=14),
            ),
            _atr(),
        ),
        when=AllCondition(
            all=(
                ComparisonCondition(
                    left=IndicatorOperand(indicator="rsi"),
                    operator=ComparisonOperator.LTE,
                    right=LiteralOperand(literal="30"),
                ),
            )
        ),
        tags=("template", StrategyTemplateId.RSI_MEAN_REVERSION.value),
    )


def _macd_trend(
    strategy_id: UUID,
    created_at: datetime,
    instrument: Instrument,
    timeframe: DatasetTimeframe,
) -> StrategyDefinition:
    """Long when MACD line crosses above its signal. Warmup is slow+signal-1."""
    return _draft(
        strategy_id=strategy_id,
        created_at=created_at,
        instrument=instrument,
        timeframe=timeframe,
        name=_template_name(instrument.product_id, timeframe, "MACD trend"),
        description="MACD trend research template; not trading authority.",
        warmup_bars=34,
        indicators=(
            IndicatorDefinition(
                id="macd",
                kind=IndicatorKind.MACD,
                input="close",
                parameters=MacdIndicatorParameters(fast_period=12, slow_period=26, signal_period=9),
            ),
            _atr(),
        ),
        when=AllCondition(
            all=(
                ComparisonCondition(
                    left=IndicatorOperand(indicator="macd", series="macd"),
                    operator=ComparisonOperator.CROSSES_ABOVE,
                    right=IndicatorOperand(indicator="macd", series="signal"),
                ),
            )
        ),
        tags=("template", StrategyTemplateId.MACD_TREND.value),
    )


def _bollinger_mean_reversion(
    strategy_id: UUID,
    created_at: datetime,
    instrument: Instrument,
    timeframe: DatasetTimeframe,
) -> StrategyDefinition:
    """Long when close is at or below the lower Bollinger band."""
    return _draft(
        strategy_id=strategy_id,
        created_at=created_at,
        instrument=instrument,
        timeframe=timeframe,
        name=_template_name(instrument.product_id, timeframe, "Bollinger mean reversion"),
        description="Bollinger mean-reversion research template; not trading authority.",
        warmup_bars=20,
        indicators=(
            IndicatorDefinition(
                id="close",
                kind=IndicatorKind.IDENTITY,
                input="close",
                parameters=EmptyIndicatorParameters(),
            ),
            IndicatorDefinition(
                id="bands",
                kind=IndicatorKind.BOLLINGER,
                input="close",
                parameters=BollingerIndicatorParameters(period=20, stdev_multiplier="2"),
            ),
            _atr(),
        ),
        when=AllCondition(
            all=(
                ComparisonCondition(
                    left=IndicatorOperand(indicator="close"),
                    operator=ComparisonOperator.LTE,
                    right=IndicatorOperand(indicator="bands", series="lower"),
                ),
            )
        ),
        tags=("template", StrategyTemplateId.BOLLINGER_MEAN_REVERSION.value),
    )


def _donchian_breakout(
    strategy_id: UUID,
    created_at: datetime,
    instrument: Instrument,
    timeframe: DatasetTimeframe,
) -> StrategyDefinition:
    """Long when close crosses above the previous 20-bar Donchian high (offset 1)."""
    return _draft(
        strategy_id=strategy_id,
        created_at=created_at,
        instrument=instrument,
        timeframe=timeframe,
        name=_template_name(instrument.product_id, timeframe, "Donchian breakout"),
        description="Donchian channel breakout research template; not trading authority.",
        warmup_bars=21,
        indicators=(
            _close(),
            IndicatorDefinition(
                id="channel",
                kind=IndicatorKind.DONCHIAN,
                input=("high", "low"),
                parameters=IndicatorParameters(period=20),
                offset=1,
            ),
            _atr(),
        ),
        when=AllCondition(
            all=(
                ComparisonCondition(
                    left=IndicatorOperand(indicator="close"),
                    operator=ComparisonOperator.CROSSES_ABOVE,
                    right=IndicatorOperand(indicator="channel", series="upper"),
                ),
            )
        ),
        tags=("template", StrategyTemplateId.DONCHIAN_BREAKOUT.value),
    )


def _supertrend_trend(
    strategy_id: UUID,
    created_at: datetime,
    instrument: Instrument,
    timeframe: DatasetTimeframe,
) -> StrategyDefinition:
    """Long when Supertrend flips up (direction crosses 0) while ADX shows a trend."""
    return _draft(
        strategy_id=strategy_id,
        created_at=created_at,
        instrument=instrument,
        timeframe=timeframe,
        name=_template_name(instrument.product_id, timeframe, "Supertrend trend"),
        description="Supertrend trend-following research template; not trading authority.",
        warmup_bars=27,
        indicators=(
            IndicatorDefinition(
                id="trend",
                kind=IndicatorKind.SUPERTREND,
                input=("high", "low", "close"),
                parameters=SupertrendIndicatorParameters(period=10, multiplier="3"),
            ),
            IndicatorDefinition(
                id="zero",
                kind=IndicatorKind.CONSTANT,
                parameters=ConstantIndicatorParameters(value="0"),
            ),
            _adx(),
            _atr(),
        ),
        when=AllCondition(
            all=(
                ComparisonCondition(
                    left=IndicatorOperand(indicator="trend", series="direction"),
                    operator=ComparisonOperator.CROSSES_ABOVE,
                    right=IndicatorOperand(indicator="zero"),
                ),
                ComparisonCondition(
                    left=IndicatorOperand(indicator="adx", series="adx"),
                    operator=ComparisonOperator.GTE,
                    right=LiteralOperand(literal="20"),
                ),
            )
        ),
        tags=("template", StrategyTemplateId.SUPERTREND_TREND.value),
    )


def _squeeze_breakout(
    strategy_id: UUID,
    created_at: datetime,
    instrument: Instrument,
    timeframe: DatasetTimeframe,
) -> StrategyDefinition:
    """Long on an upper-band breakout right after Bollinger sat inside Keltner."""
    return _draft(
        strategy_id=strategy_id,
        created_at=created_at,
        instrument=instrument,
        timeframe=timeframe,
        name=_template_name(instrument.product_id, timeframe, "Squeeze breakout"),
        description="Bollinger/Keltner squeeze breakout research template; not trading authority.",
        warmup_bars=21,
        indicators=(
            _close(),
            _bands("bands", offset=None),
            _keltner("channel", offset=None),
            _bands("prior_bands", offset=1),
            _keltner("prior_channel", offset=1),
            _atr(),
        ),
        when=AllCondition(
            all=(
                ComparisonCondition(
                    left=IndicatorOperand(indicator="prior_bands", series="upper"),
                    operator=ComparisonOperator.LT,
                    right=IndicatorOperand(indicator="prior_channel", series="upper"),
                ),
                ComparisonCondition(
                    left=IndicatorOperand(indicator="prior_bands", series="lower"),
                    operator=ComparisonOperator.GT,
                    right=IndicatorOperand(indicator="prior_channel", series="lower"),
                ),
                ComparisonCondition(
                    left=IndicatorOperand(indicator="close"),
                    operator=ComparisonOperator.CROSSES_ABOVE,
                    right=IndicatorOperand(indicator="bands", series="upper"),
                ),
            )
        ),
        tags=("template", StrategyTemplateId.SQUEEZE_BREAKOUT.value),
    )


def _zscore_mean_reversion(
    strategy_id: UUID,
    created_at: datetime,
    instrument: Instrument,
    timeframe: DatasetTimeframe,
) -> StrategyDefinition:
    """Long on a -2 close z-score when ADX or Choppiness says the market is ranging."""
    return _draft(
        strategy_id=strategy_id,
        created_at=created_at,
        instrument=instrument,
        timeframe=timeframe,
        name=_template_name(instrument.product_id, timeframe, "Z-score mean reversion"),
        description="Z-score mean-reversion research template; not trading authority.",
        warmup_bars=27,
        indicators=(
            IndicatorDefinition(
                id="z",
                kind=IndicatorKind.ZSCORE,
                input="close",
                parameters=IndicatorParameters(period=20),
            ),
            _adx(),
            IndicatorDefinition(
                id="chop",
                kind=IndicatorKind.CHOPPINESS,
                input=("high", "low", "close"),
                parameters=IndicatorParameters(period=14),
            ),
            _atr(),
        ),
        when=AllCondition(
            all=(
                ComparisonCondition(
                    left=IndicatorOperand(indicator="z"),
                    operator=ComparisonOperator.LTE,
                    right=LiteralOperand(literal="-2"),
                ),
                AnyCondition(
                    any=(
                        ComparisonCondition(
                            left=IndicatorOperand(indicator="adx", series="adx"),
                            operator=ComparisonOperator.LT,
                            right=LiteralOperand(literal="20"),
                        ),
                        ComparisonCondition(
                            left=IndicatorOperand(indicator="chop"),
                            operator=ComparisonOperator.GT,
                            right=LiteralOperand(literal="61.8"),
                        ),
                    )
                ),
            )
        ),
        tags=("template", StrategyTemplateId.ZSCORE_MEAN_REVERSION.value),
    )


def _close() -> IndicatorDefinition:
    """Identity close used as a crossover operand."""
    return IndicatorDefinition(
        id="close",
        kind=IndicatorKind.IDENTITY,
        input="close",
        parameters=EmptyIndicatorParameters(),
    )


def _adx() -> IndicatorDefinition:
    """ADX(14) trend-strength filter."""
    return IndicatorDefinition(
        id="adx",
        kind=IndicatorKind.ADX,
        input=("high", "low", "close"),
        parameters=IndicatorParameters(period=14),
    )


def _bands(indicator_id: str, *, offset: int | None) -> IndicatorDefinition:
    """Bollinger(20, 2) on close, optionally lagged."""
    return IndicatorDefinition(
        id=indicator_id,
        kind=IndicatorKind.BOLLINGER,
        input="close",
        parameters=BollingerIndicatorParameters(period=20, stdev_multiplier="2"),
        offset=offset,
    )


def _keltner(indicator_id: str, *, offset: int | None) -> IndicatorDefinition:
    """Keltner(20, ATR 10, 1.5) channel, optionally lagged."""
    return IndicatorDefinition(
        id=indicator_id,
        kind=IndicatorKind.KELTNER,
        input=("high", "low", "close"),
        parameters=KeltnerIndicatorParameters(period=20, atr_period=10, multiplier="1.5"),
        offset=offset,
    )


def _atr() -> IndicatorDefinition:
    """Shared ATR(14) used by every template's initial stop."""
    return IndicatorDefinition(
        id="atr",
        kind=IndicatorKind.ATR,
        input=("high", "low", "close"),
        parameters=IndicatorParameters(period=14),
    )


def _draft(
    *,
    strategy_id: UUID,
    created_at: datetime,
    instrument: Instrument,
    timeframe: DatasetTimeframe,
    name: str,
    description: str,
    warmup_bars: int,
    indicators: tuple[IndicatorDefinition, ...],
    when: AllCondition,
    tags: tuple[str, ...],
) -> StrategyDefinition:
    """Assemble shared long-only sizing, exits, and execution for one template."""
    created = created_at.astimezone(UTC)
    return StrategyDefinition(
        schema_version="1.0",
        strategy_id=strategy_id,
        name=name,
        description=description,
        created_at=created,
        instrument=instrument,
        timeframe=timeframe,
        data_requirements=DataRequirements(
            warmup_bars=warmup_bars,
            required_fields=_OHLCV,
        ),
        indicators=indicators,
        entry=EntryDefinition(
            side="long",
            when=when,
            cooldown_bars=3,
            max_open_positions=1,
        ),
        sizing=RiskFractionSizing(
            kind="risk_fraction",
            risk_fraction="0.005",
            min_quote_notional="10",
            max_quote_notional="100",
        ),
        portfolio_limits=PortfolioLimits(
            max_strategy_exposure_fraction="0.10", max_concurrent_positions=1
        ),
        exits=ExitDefinition(
            initial_stop=AtrMultipleStop(kind="atr_multiple", atr_indicator="atr", multiple="2"),
            take_profit=RewardRiskTakeProfit(kind="reward_risk", multiple="2"),
            trailing_stop=DisabledTrailingStop(enabled=False),
            time_exit=TimeExit(max_bars_held=96),
        ),
        execution=ExecutionPreferences(
            entry_preference="maker_only", max_entry_wait_bars=2, on_unfilled_entry="cancel"
        ),
        metadata=StrategyMetadata(tags=tags, notes=()),
    )


_TEMPLATE_BUILDERS: dict[
    StrategyTemplateId,
    Callable[[UUID, datetime, Instrument, DatasetTimeframe], StrategyDefinition],
] = {
    StrategyTemplateId.EMA_TREND: _ema_trend,
    StrategyTemplateId.RSI_MEAN_REVERSION: _rsi_mean_reversion,
    StrategyTemplateId.MACD_TREND: _macd_trend,
    StrategyTemplateId.BOLLINGER_MEAN_REVERSION: _bollinger_mean_reversion,
    StrategyTemplateId.DONCHIAN_BREAKOUT: _donchian_breakout,
    StrategyTemplateId.SUPERTREND_TREND: _supertrend_trend,
    StrategyTemplateId.SQUEEZE_BREAKOUT: _squeeze_breakout,
    StrategyTemplateId.ZSCORE_MEAN_REVERSION: _zscore_mean_reversion,
}


def _template_name(product_id: str, timeframe: str, kind_label: str) -> str:
    """Keep the historical BTC 1h EMA title; otherwise name product, bar, and template."""
    if product_id == "BTC-USD" and timeframe == "1h" and kind_label == "EMA trend":
        return "BTC hourly EMA trend"
    pretty = "hourly" if timeframe == "1h" else timeframe
    base = product_id.split("-", 1)[0]
    return f"{base} {pretty} {kind_label}"
