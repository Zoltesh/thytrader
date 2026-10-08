"""Shared building blocks for the draft templates.

Stock indicator declarations (close, ADX, Bollinger, Keltner, ATR), the draft assembler
that applies the conservative long-only sizing, exits, and execution every template
shares, and the template naming rule.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from thytrader.strategies.models import (
    AllCondition,
    AtrMultipleStop,
    BollingerIndicatorParameters,
    DataRequirements,
    DisabledTrailingStop,
    EmptyIndicatorParameters,
    EntryDefinition,
    ExecutionPreferences,
    ExitDefinition,
    IndicatorDefinition,
    IndicatorKind,
    IndicatorParameters,
    KeltnerIndicatorParameters,
    PortfolioLimits,
    ReferenceInstrument,
    RewardRiskTakeProfit,
    RiskFractionSizing,
    StrategyDefinition,
    StrategyMetadata,
    TimeExit,
)

if TYPE_CHECKING:
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
    exits: ExitDefinition | None = None,
    reference_instruments: tuple[ReferenceInstrument, ...] = (),
) -> StrategyDefinition:
    """Assemble shared long-only sizing, exits, and execution for one template.

    ``exits`` replaces the shared 2x ATR stop / 2R target / 96-bar exits when given.
    ``reference_instruments`` declares read-only reference series (ADR 0096).
    """
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
            reference_instruments=reference_instruments,
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
        exits=exits
        or ExitDefinition(
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
