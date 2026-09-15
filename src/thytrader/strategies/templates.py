"""Named conservative draft templates for research authoring."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from thytrader.strategies.models import (
    AllCondition,
    AtrMultipleStop,
    BollingerIndicatorParameters,
    ComparisonCondition,
    ComparisonOperator,
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
    LiteralOperand,
    MacdIndicatorParameters,
    PortfolioLimits,
    RewardRiskTakeProfit,
    RiskFractionSizing,
    StrategyDefinition,
    StrategyMetadata,
    StrategyStatus,
    TimeExit,
)

if TYPE_CHECKING:
    from uuid import UUID

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
    )


def parse_template_id(value: str) -> StrategyTemplateId:
    """Reject unknown template ids before constructing a draft."""
    try:
        return StrategyTemplateId(value)
    except ValueError:
        allowed = ", ".join(item.value for item in StrategyTemplateId)
        message = f"Unknown strategy template. Use one of: {allowed}."
        raise ValueError(message) from None


def build_template_draft(
    *,
    template_id: StrategyTemplateId,
    strategy_id: UUID,
    created_at: datetime,
    instrument: Instrument,
    timeframe: Literal["1h", "5m"],
) -> StrategyDefinition:
    """Return one validated draft for the selected template."""
    if template_id is StrategyTemplateId.EMA_TREND:
        return _ema_trend(strategy_id, created_at, instrument, timeframe)
    if template_id is StrategyTemplateId.RSI_MEAN_REVERSION:
        return _rsi_mean_reversion(strategy_id, created_at, instrument, timeframe)
    if template_id is StrategyTemplateId.MACD_TREND:
        return _macd_trend(strategy_id, created_at, instrument, timeframe)
    return _bollinger_mean_reversion(strategy_id, created_at, instrument, timeframe)


def _ema_trend(
    strategy_id: UUID,
    created_at: datetime,
    instrument: Instrument,
    timeframe: Literal["1h", "5m"],
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
    timeframe: Literal["1h", "5m"],
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
    timeframe: Literal["1h", "5m"],
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
    timeframe: Literal["1h", "5m"],
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
    timeframe: Literal["1h", "5m"],
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
        version=1,
        name=name,
        description=description,
        status=StrategyStatus.DRAFT,
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


def _template_name(product_id: str, timeframe: str, kind_label: str) -> str:
    """Keep the historical BTC 1h EMA title; otherwise name product, bar, and template."""
    if product_id == "BTC-USD" and timeframe == "1h" and kind_label == "EMA trend":
        return "BTC hourly EMA trend"
    pretty = "hourly" if timeframe == "1h" else timeframe
    base = product_id.split("-", 1)[0]
    return f"{base} {pretty} {kind_label}"
