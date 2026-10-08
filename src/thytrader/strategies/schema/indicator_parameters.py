"""Indicator kind catalog and the per-kind indicator parameter blocks."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from thytrader.strategies.schema.primitives import DecimalText, _FrozenModel


class IndicatorParameters(_FrozenModel):
    """Bounded period parameters shared by the implemented rolling indicator profile."""

    period: int = Field(ge=2, le=500)


class MacdIndicatorParameters(_FrozenModel):
    """Close-locked EMA windows shared by MACD and PPO: fast, slow, and signal smoothing."""

    fast_period: int = Field(ge=2, le=500)
    slow_period: int = Field(ge=2, le=500)
    signal_period: int = Field(ge=2, le=500)

    @model_validator(mode="after")
    def validate_fast_shorter_than_slow(self) -> Self:
        """Require the fast EMA window to be strictly shorter than the slow window."""
        if self.fast_period >= self.slow_period:
            raise ValueError("macd/ppo fast_period must be less than slow_period")
        return self


def _require_band_multiplier(value: str, *, name: str) -> None:
    """Reject non-positive or unbounded band-width multipliers (exclusive 0, inclusive 10)."""
    parsed = Decimal(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than 0")
    if parsed > Decimal(10):
        raise ValueError(f"{name} must be at most 10")


class BollingerIndicatorParameters(_FrozenModel):
    """Close-locked SMA period and population-stdev band multiplier (Bollinger, %B, bandwidth)."""

    period: int = Field(ge=2, le=500)
    stdev_multiplier: DecimalText

    @model_validator(mode="after")
    def validate_multiplier_bounds(self) -> Self:
        """Reject non-positive or unbounded Bollinger width multipliers."""
        _require_band_multiplier(self.stdev_multiplier, name="bollinger stdev_multiplier")
        return self


class StochasticIndicatorParameters(_FrozenModel):
    """HLC-locked fast stochastic windows: raw %K lookback and %D SMA."""

    k_period: int = Field(ge=2, le=100)
    d_period: int = Field(ge=2, le=500)


class ConstantIndicatorParameters(_FrozenModel):
    """Named finite level repeated on every completed bar."""

    value: DecimalText


class EmptyIndicatorParameters(_FrozenModel):
    """No rolling or level parameters; used by identity OHLCV kinds."""


class KamaIndicatorParameters(_FrozenModel):
    """Kaufman adaptive MA: efficiency-ratio window plus fast and slow smoothing periods."""

    period: int = Field(ge=2, le=100)
    fast_period: int = Field(ge=2, le=100)
    slow_period: int = Field(ge=2, le=500)

    @model_validator(mode="after")
    def validate_fast_shorter_than_slow(self) -> Self:
        """Require the fast smoothing period to be strictly shorter than the slow one."""
        if self.fast_period >= self.slow_period:
            raise ValueError("kama fast_period must be less than slow_period")
        return self


class SupertrendIndicatorParameters(_FrozenModel):
    """HLC-locked Supertrend: Wilder ATR period and band multiplier."""

    period: int = Field(ge=2, le=100)
    multiplier: DecimalText

    @model_validator(mode="after")
    def validate_multiplier_bounds(self) -> Self:
        """Reject non-positive or unbounded band multipliers."""
        _require_band_multiplier(self.multiplier, name="supertrend multiplier")
        return self


class ParabolicSarIndicatorParameters(_FrozenModel):
    """High/low-locked Parabolic SAR acceleration step and acceleration cap."""

    step: DecimalText
    max_step: DecimalText

    @model_validator(mode="after")
    def validate_acceleration_bounds(self) -> Self:
        """Require 0 < step <= max_step <= 1 so the SAR never overshoots its extreme point."""
        step = Decimal(self.step)
        max_step = Decimal(self.max_step)
        if step <= 0:
            raise ValueError("parabolic_sar step must be greater than 0")
        if max_step > 1:
            raise ValueError("parabolic_sar max_step must be at most 1")
        if step > max_step:
            raise ValueError("parabolic_sar step must be at most max_step")
        return self


class IchimokuIndicatorParameters(_FrozenModel):
    """High/low-locked Ichimoku conversion, base, and leading-span-B windows."""

    tenkan_period: int = Field(ge=2, le=500)
    kijun_period: int = Field(ge=2, le=500)
    senkou_b_period: int = Field(ge=2, le=500)

    @model_validator(mode="after")
    def validate_ordered_windows(self) -> Self:
        """Require tenkan < kijun < senkou_b, the conventional short-to-long ordering."""
        if not self.tenkan_period < self.kijun_period < self.senkou_b_period:
            raise ValueError(
                "ichimoku periods must satisfy tenkan_period < kijun_period < senkou_b_period"
            )
        return self


class StochasticRsiIndicatorParameters(_FrozenModel):
    """Close-locked stochastic RSI: RSI window, stochastic window, and %K / %D smoothing."""

    rsi_period: int = Field(ge=2, le=100)
    stoch_period: int = Field(ge=2, le=100)
    k_period: int = Field(ge=1, le=100)
    d_period: int = Field(ge=1, le=100)


class UltimateOscillatorIndicatorParameters(_FrozenModel):
    """HLC-locked Ultimate Oscillator short, medium, and long buying-pressure windows."""

    short_period: int = Field(ge=2, le=100)
    medium_period: int = Field(ge=2, le=100)
    long_period: int = Field(ge=2, le=100)

    @model_validator(mode="after")
    def validate_ordered_windows(self) -> Self:
        """Require short < medium < long windows."""
        if not self.short_period < self.medium_period < self.long_period:
            raise ValueError(
                "ultimate_oscillator periods must satisfy short_period < medium_period < "
                "long_period"
            )
        return self


class AwesomeOscillatorIndicatorParameters(_FrozenModel):
    """High/low-locked Awesome Oscillator fast and slow median-price SMA windows."""

    fast_period: int = Field(ge=2, le=500)
    slow_period: int = Field(ge=2, le=500)

    @model_validator(mode="after")
    def validate_fast_shorter_than_slow(self) -> Self:
        """Require the fast SMA window to be strictly shorter than the slow window."""
        if self.fast_period >= self.slow_period:
            raise ValueError("awesome_oscillator fast_period must be less than slow_period")
        return self


class TsiIndicatorParameters(_FrozenModel):
    """Close-locked True Strength Index double-smoothing and signal windows."""

    long_period: int = Field(ge=2, le=500)
    short_period: int = Field(ge=2, le=500)
    signal_period: int = Field(ge=2, le=500)

    @model_validator(mode="after")
    def validate_short_shorter_than_long(self) -> Self:
        """Require the second (short) smoothing to be strictly shorter than the first."""
        if self.short_period >= self.long_period:
            raise ValueError("tsi short_period must be less than long_period")
        return self


class KeltnerIndicatorParameters(_FrozenModel):
    """HLC-locked Keltner channel: EMA middle period, Wilder ATR period, band multiplier."""

    period: int = Field(ge=2, le=500)
    atr_period: int = Field(ge=2, le=100)
    multiplier: DecimalText

    @model_validator(mode="after")
    def validate_multiplier_bounds(self) -> Self:
        """Reject non-positive or unbounded band multipliers."""
        _require_band_multiplier(self.multiplier, name="keltner multiplier")
        return self


class HistoricalVolatilityIndicatorParameters(_FrozenModel):
    """Close-locked historical volatility window with explicit annualization periods."""

    period: int = Field(ge=2, le=500)
    annualization_periods: int = Field(ge=1, le=525_600)


class SignalLineIndicatorParameters(_FrozenModel):
    """Signal-line SMA period over a cumulative series (OBV, accumulation/distribution)."""

    signal_period: int = Field(ge=2, le=500)


IndicatorParameterBlock = (
    MacdIndicatorParameters
    | BollingerIndicatorParameters
    | StochasticIndicatorParameters
    | IndicatorParameters
    | ConstantIndicatorParameters
    | EmptyIndicatorParameters
    | KamaIndicatorParameters
    | SupertrendIndicatorParameters
    | ParabolicSarIndicatorParameters
    | IchimokuIndicatorParameters
    | StochasticRsiIndicatorParameters
    | UltimateOscillatorIndicatorParameters
    | AwesomeOscillatorIndicatorParameters
    | TsiIndicatorParameters
    | KeltnerIndicatorParameters
    | HistoricalVolatilityIndicatorParameters
    | SignalLineIndicatorParameters
)


class IndicatorKind(StrEnum):
    """Fail-closed kinds in the canonical indicator registry."""

    EMA = "ema"
    SMA = "sma"
    RSI = "rsi"
    ATR = "atr"
    VOLUME_SMA = "volume_sma"
    HIGHEST = "highest"
    LOWEST = "lowest"
    STDEV = "stdev"
    ROC = "roc"
    WILLIAMS_R = "williams_r"
    CCI = "cci"
    IDENTITY = "identity"
    CONSTANT = "constant"
    WMA = "wma"
    MOMENTUM = "momentum"
    MFI = "mfi"
    MACD = "macd"
    BOLLINGER = "bollinger"
    STDEV_SAMPLE = "stdev_sample"
    STOCHASTIC = "stochastic"
    ADX = "adx"
    DEMA = "dema"
    TEMA = "tema"
    HMA = "hma"
    KAMA = "kama"
    VWMA = "vwma"
    SUPERTREND = "supertrend"
    PARABOLIC_SAR = "parabolic_sar"
    AROON = "aroon"
    ICHIMOKU = "ichimoku"
    VORTEX = "vortex"
    LINEAR_REGRESSION = "linear_regression"
    TRIX = "trix"
    STOCHASTIC_RSI = "stochastic_rsi"
    PPO = "ppo"
    ULTIMATE_OSCILLATOR = "ultimate_oscillator"
    AWESOME_OSCILLATOR = "awesome_oscillator"
    CMO = "cmo"
    TSI = "tsi"
    KELTNER = "keltner"
    DONCHIAN = "donchian"
    BOLLINGER_PERCENT_B = "bollinger_percent_b"
    BOLLINGER_BANDWIDTH = "bollinger_bandwidth"
    NATR = "natr"
    CHOPPINESS = "choppiness"
    HISTORICAL_VOLATILITY = "historical_volatility"
    OBV = "obv"
    CMF = "cmf"
    ACCUMULATION_DISTRIBUTION = "accumulation_distribution"
    VWAP = "vwap"
    FORCE_INDEX = "force_index"
    ZSCORE = "zscore"
    PERCENT_RANK = "percent_rank"
