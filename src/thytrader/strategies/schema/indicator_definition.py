"""Indicator declarations: inputs, output series, parameter shapes, and validation."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from thytrader.market_data.models import DatasetTimeframe
from thytrader.strategies.schema.indicator_parameters import (
    AwesomeOscillatorIndicatorParameters,
    BollingerIndicatorParameters,
    ConstantIndicatorParameters,
    EmptyIndicatorParameters,
    HistoricalVolatilityIndicatorParameters,
    IchimokuIndicatorParameters,
    IndicatorKind,
    IndicatorParameterBlock,
    IndicatorParameters,
    KamaIndicatorParameters,
    KeltnerIndicatorParameters,
    MacdIndicatorParameters,
    ParabolicSarIndicatorParameters,
    SignalLineIndicatorParameters,
    StochasticIndicatorParameters,
    StochasticRsiIndicatorParameters,
    SupertrendIndicatorParameters,
    TsiIndicatorParameters,
    UltimateOscillatorIndicatorParameters,
)
from thytrader.strategies.schema.market import REFERENCE_ID_PATTERN
from thytrader.strategies.schema.primitives import _FrozenModel

HLC_INPUT: tuple[Literal["high"], Literal["low"], Literal["close"]] = ("high", "low", "close")
HLCV_INPUT: tuple[Literal["high"], Literal["low"], Literal["close"], Literal["volume"]] = (
    "high",
    "low",
    "close",
    "volume",
)
HL_INPUT: tuple[Literal["high"], Literal["low"]] = ("high", "low")
CLOSE_VOLUME_INPUT: tuple[Literal["close"], Literal["volume"]] = ("close", "volume")
MAX_INDICATOR_OFFSET = 500
"""Largest bar lag one indicator declaration may request."""

_SINGLE_SOURCE_INPUT: dict[IndicatorKind, Literal["high", "low", "close", "volume"]] = {
    IndicatorKind.RSI: "close",
    IndicatorKind.VOLUME_SMA: "volume",
    IndicatorKind.MACD: "close",
    IndicatorKind.BOLLINGER: "close",
    IndicatorKind.TRIX: "close",
    IndicatorKind.STOCHASTIC_RSI: "close",
    IndicatorKind.PPO: "close",
    IndicatorKind.CMO: "close",
    IndicatorKind.TSI: "close",
    IndicatorKind.BOLLINGER_PERCENT_B: "close",
    IndicatorKind.BOLLINGER_BANDWIDTH: "close",
    IndicatorKind.HISTORICAL_VOLATILITY: "close",
}
_CONFIGURABLE_SINGLE_SOURCE_KINDS = frozenset(
    {
        IndicatorKind.EMA,
        IndicatorKind.SMA,
        IndicatorKind.HIGHEST,
        IndicatorKind.LOWEST,
        IndicatorKind.STDEV,
        IndicatorKind.STDEV_SAMPLE,
        IndicatorKind.ROC,
        IndicatorKind.WMA,
        IndicatorKind.MOMENTUM,
        IndicatorKind.DEMA,
        IndicatorKind.TEMA,
        IndicatorKind.HMA,
        IndicatorKind.KAMA,
        IndicatorKind.LINEAR_REGRESSION,
        IndicatorKind.ZSCORE,
        IndicatorKind.PERCENT_RANK,
    }
)
MACD_OUTPUT_SERIES: tuple[str, ...] = ("macd", "signal", "histogram")
BOLLINGER_OUTPUT_SERIES: tuple[str, ...] = ("middle", "upper", "lower")
STOCHASTIC_OUTPUT_SERIES: tuple[str, ...] = ("k", "d")
ADX_OUTPUT_SERIES: tuple[str, ...] = ("adx", "plus_di", "minus_di")
CHANNEL_OUTPUT_SERIES: tuple[str, ...] = ("upper", "middle", "lower")
_INDICATOR_OUTPUT_SERIES: dict[IndicatorKind, tuple[str, ...]] = {
    IndicatorKind.MACD: MACD_OUTPUT_SERIES,
    IndicatorKind.BOLLINGER: BOLLINGER_OUTPUT_SERIES,
    IndicatorKind.STOCHASTIC: STOCHASTIC_OUTPUT_SERIES,
    IndicatorKind.ADX: ADX_OUTPUT_SERIES,
    IndicatorKind.SUPERTREND: ("value", "direction"),
    IndicatorKind.AROON: ("up", "down", "oscillator"),
    IndicatorKind.ICHIMOKU: ("tenkan", "kijun", "senkou_a", "senkou_b"),
    IndicatorKind.VORTEX: ("plus", "minus"),
    IndicatorKind.LINEAR_REGRESSION: ("value", "slope"),
    IndicatorKind.STOCHASTIC_RSI: STOCHASTIC_OUTPUT_SERIES,
    IndicatorKind.PPO: ("ppo", "signal", "histogram"),
    IndicatorKind.TSI: ("tsi", "signal"),
    IndicatorKind.KELTNER: CHANNEL_OUTPUT_SERIES,
    IndicatorKind.DONCHIAN: CHANNEL_OUTPUT_SERIES,
    IndicatorKind.OBV: ("obv", "signal"),
    IndicatorKind.ACCUMULATION_DISTRIBUTION: ("ad", "signal"),
}
_LOCKED_TUPLE_INPUT: dict[IndicatorKind, tuple[str, ...]] = {
    IndicatorKind.ATR: HLC_INPUT,
    IndicatorKind.WILLIAMS_R: HLC_INPUT,
    IndicatorKind.CCI: HLC_INPUT,
    IndicatorKind.STOCHASTIC: HLC_INPUT,
    IndicatorKind.ADX: HLC_INPUT,
    IndicatorKind.MFI: HLCV_INPUT,
    IndicatorKind.VWMA: CLOSE_VOLUME_INPUT,
    IndicatorKind.SUPERTREND: HLC_INPUT,
    IndicatorKind.PARABOLIC_SAR: HL_INPUT,
    IndicatorKind.AROON: HL_INPUT,
    IndicatorKind.ICHIMOKU: HL_INPUT,
    IndicatorKind.VORTEX: HLC_INPUT,
    IndicatorKind.ULTIMATE_OSCILLATOR: HLC_INPUT,
    IndicatorKind.AWESOME_OSCILLATOR: HL_INPUT,
    IndicatorKind.KELTNER: HLC_INPUT,
    IndicatorKind.DONCHIAN: HL_INPUT,
    IndicatorKind.NATR: HLC_INPUT,
    IndicatorKind.CHOPPINESS: HLC_INPUT,
    IndicatorKind.OBV: CLOSE_VOLUME_INPUT,
    IndicatorKind.CMF: HLCV_INPUT,
    IndicatorKind.ACCUMULATION_DISTRIBUTION: HLCV_INPUT,
    IndicatorKind.VWAP: HLCV_INPUT,
    IndicatorKind.FORCE_INDEX: CLOSE_VOLUME_INPUT,
}
_SHORT_PERIOD_KINDS = frozenset(
    {
        IndicatorKind.RSI,
        IndicatorKind.ATR,
        IndicatorKind.WILLIAMS_R,
        IndicatorKind.CCI,
        IndicatorKind.MFI,
        IndicatorKind.ADX,
        IndicatorKind.CMO,
        IndicatorKind.NATR,
    }
)


_IDENTITY_INPUTS = frozenset({"open", "high", "low", "close", "volume"})


class IndicatorDefinition(_FrozenModel):
    """One named declarative indicator with no executable expression surface."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: IndicatorKind
    input: (
        Literal["open", "high", "low", "close", "volume"]
        | tuple[
            Literal["high"],
            Literal["low"],
            Literal["close"],
        ]
        | tuple[
            Literal["high"],
            Literal["low"],
            Literal["close"],
            Literal["volume"],
        ]
        | tuple[Literal["high"], Literal["low"]]
        | tuple[Literal["close"], Literal["volume"]]
        | None
    ) = None
    parameters: IndicatorParameterBlock
    timeframe: DatasetTimeframe | None = Field(default=None, exclude_if=lambda value: value is None)
    offset: int | None = Field(
        default=None,
        ge=0,
        le=MAX_INDICATOR_OFFSET,
        exclude_if=lambda value: value is None,
        description=(
            "Bar lag on this indicator's own clock: every value is the one from `offset` "
            "completed bars earlier. Omitted (or 0) means the current completed bar."
        ),
    )
    source: str | None = Field(
        default=None,
        pattern=REFERENCE_ID_PATTERN,
        exclude_if=lambda value: value is None,
        description=(
            "Reference instrument id (data_requirements.reference_instruments[].id) whose "
            "closed bars this indicator reads, on that reference's timeframe. Omitted means "
            "the traded instrument (ADR 0096)."
        ),
    )

    @field_validator("offset")
    @classmethod
    def normalize_zero_offset(cls, value: int | None) -> int | None:
        """Treat ``offset: 0`` as omitted so it can never change canonical bytes."""
        return None if value == 0 else value

    @model_validator(mode="after")
    def validate_kind_period(self) -> Self:
        """Apply conservative V1 period bounds and locked inputs by indicator kind."""
        if self.source is not None and self.timeframe is not None:
            raise ValueError(
                "an indicator with source must omit timeframe: it reads the reference "
                "instrument's timeframe"
            )
        if self.kind is IndicatorKind.IDENTITY:
            _require_identity_indicator(self)
            return self
        if self.kind is IndicatorKind.CONSTANT:
            _require_constant_indicator(self)
            return self
        shape = _PARAMETER_SHAPES.get(self.kind)
        if shape is None:
            _require_period_indicator(self)
            return self
        models, message = shape
        if not isinstance(self.parameters, models):
            raise ValueError(message)  # noqa: TRY004 - pydantic reports ValueError as invalid.
        _require_locked_source(self)
        return self


def indicator_output_series(kind: IndicatorKind) -> tuple[str, ...] | None:
    """Return declared output names for multi-series kinds, or None for a single value."""
    return _INDICATOR_OUTPUT_SERIES.get(kind)


def indicator_value_keys(indicator: IndicatorDefinition) -> tuple[str, ...]:
    """Return evaluator and trace keys for one indicator's output series."""
    series = indicator_output_series(indicator.kind)
    if series is None:
        return (indicator.id,)
    return tuple(f"{indicator.id}.{name}" for name in series)


_PARAMETER_SHAPES: dict[IndicatorKind, tuple[tuple[type[_FrozenModel], ...], str]] = {
    IndicatorKind.MACD: (
        (MacdIndicatorParameters,),
        "macd parameters must declare fast_period, slow_period, and signal_period",
    ),
    IndicatorKind.PPO: (
        (MacdIndicatorParameters,),
        "ppo parameters must declare fast_period, slow_period, and signal_period",
    ),
    IndicatorKind.BOLLINGER: (
        (BollingerIndicatorParameters,),
        "bollinger parameters must declare period and stdev_multiplier",
    ),
    IndicatorKind.BOLLINGER_PERCENT_B: (
        (BollingerIndicatorParameters,),
        "bollinger_percent_b parameters must declare period and stdev_multiplier",
    ),
    IndicatorKind.BOLLINGER_BANDWIDTH: (
        (BollingerIndicatorParameters,),
        "bollinger_bandwidth parameters must declare period and stdev_multiplier",
    ),
    IndicatorKind.STOCHASTIC: (
        (StochasticIndicatorParameters,),
        "stochastic parameters must declare k_period and d_period",
    ),
    IndicatorKind.KAMA: (
        (KamaIndicatorParameters,),
        "kama parameters must declare period, fast_period, and slow_period",
    ),
    IndicatorKind.SUPERTREND: (
        (SupertrendIndicatorParameters,),
        "supertrend parameters must declare period and multiplier",
    ),
    IndicatorKind.PARABOLIC_SAR: (
        (ParabolicSarIndicatorParameters,),
        "parabolic_sar parameters must declare step and max_step",
    ),
    IndicatorKind.ICHIMOKU: (
        (IchimokuIndicatorParameters,),
        "ichimoku parameters must declare tenkan_period, kijun_period, and senkou_b_period",
    ),
    IndicatorKind.STOCHASTIC_RSI: (
        (StochasticRsiIndicatorParameters,),
        "stochastic_rsi parameters must declare rsi_period, stoch_period, k_period, and d_period",
    ),
    IndicatorKind.ULTIMATE_OSCILLATOR: (
        (UltimateOscillatorIndicatorParameters,),
        "ultimate_oscillator parameters must declare short_period, medium_period, and long_period",
    ),
    IndicatorKind.AWESOME_OSCILLATOR: (
        (AwesomeOscillatorIndicatorParameters,),
        "awesome_oscillator parameters must declare fast_period and slow_period",
    ),
    IndicatorKind.TSI: (
        (TsiIndicatorParameters,),
        "tsi parameters must declare long_period, short_period, and signal_period",
    ),
    IndicatorKind.KELTNER: (
        (KeltnerIndicatorParameters,),
        "keltner parameters must declare period, atr_period, and multiplier",
    ),
    IndicatorKind.HISTORICAL_VOLATILITY: (
        (IndicatorParameters, HistoricalVolatilityIndicatorParameters),
        "historical_volatility parameters must declare period and optional annualization_periods",
    ),
    IndicatorKind.OBV: (
        (SignalLineIndicatorParameters,),
        "obv parameters must declare signal_period",
    ),
    IndicatorKind.ACCUMULATION_DISTRIBUTION: (
        (SignalLineIndicatorParameters,),
        "accumulation_distribution parameters must declare signal_period",
    ),
}
"""Kinds whose parameter object is not the plain ``{period}`` block, with the fail message."""


def _require_identity_indicator(indicator: IndicatorDefinition) -> None:
    """Reject identity kinds that carry rolling parameters or a non-OHLCV source."""
    if not isinstance(indicator.parameters, EmptyIndicatorParameters):
        raise ValueError("identity parameters must be an empty object")  # noqa: TRY004
    if indicator.input not in _IDENTITY_INPUTS:
        raise ValueError("identity input must be one of open, high, low, close, volume")


def _require_constant_indicator(indicator: IndicatorDefinition) -> None:
    """Reject constant kinds that declare an input, a period, a timeframe, or a lag."""
    if not isinstance(indicator.parameters, ConstantIndicatorParameters):
        raise ValueError("constant parameters must declare value")  # noqa: TRY004
    if indicator.input is not None:
        raise ValueError("constant must omit input")
    if indicator.timeframe is not None:
        raise ValueError("constant must omit timeframe")
    if indicator.offset is not None:
        raise ValueError("constant must omit offset")
    if indicator.source is not None:
        raise ValueError("constant must omit source")


def _require_period_indicator(indicator: IndicatorDefinition) -> None:
    """Reject period kinds with the wrong parameter object, bounds, or locked source."""
    if not isinstance(indicator.parameters, IndicatorParameters):
        raise ValueError(f"{indicator.kind.value} parameters must declare period")  # noqa: TRY004
    maximum = 100 if indicator.kind in _SHORT_PERIOD_KINDS else 500
    if indicator.parameters.period > maximum:
        raise ValueError(f"{indicator.kind.name} period exceeds {maximum}")
    _require_locked_source(indicator)


def _require_locked_source(indicator: IndicatorDefinition) -> None:
    """Reject kinds whose input is not the registry-allowed OHLCV source."""
    locked = _LOCKED_TUPLE_INPUT.get(indicator.kind)
    if locked is not None:
        if indicator.input != locked:
            if indicator.kind is IndicatorKind.ATR:
                raise ValueError("ATR input must be high, low, close in canonical order")
            raise ValueError(
                f"{indicator.kind.value} input must be {', '.join(locked)} in canonical order"
            )
        return
    if indicator.kind in _CONFIGURABLE_SINGLE_SOURCE_KINDS:
        if indicator.input not in _IDENTITY_INPUTS:
            raise ValueError(
                f"{indicator.kind.value} input must be one of open, high, low, close, volume"
            )
        return
    expected = _SINGLE_SOURCE_INPUT[indicator.kind]
    if indicator.input != expected:
        raise ValueError(f"{indicator.kind.value} input must be {expected}")


def indicator_offset(indicator: IndicatorDefinition) -> int:
    """Return the declared bar lag, treating an omitted offset as the current bar (0)."""
    return 0 if indicator.offset is None else indicator.offset


def _indicator_input_fields(
    indicator: IndicatorDefinition,
) -> tuple[str, ...]:
    """Return the OHLCV fields one indicator consumes."""
    source = indicator.input
    if source is None:
        return ()
    if isinstance(source, str):
        return (source,)
    return source
