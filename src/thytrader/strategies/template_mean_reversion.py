"""Mean-reversion template builders: RSI, Bollinger band, and z-score."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.strategies.models import (
    AllCondition,
    AnyCondition,
    BollingerIndicatorParameters,
    ComparisonCondition,
    ComparisonOperator,
    EmptyIndicatorParameters,
    IndicatorDefinition,
    IndicatorKind,
    IndicatorOperand,
    IndicatorParameters,
    LiteralOperand,
    StrategyDefinition,
)
from thytrader.strategies.template_ids import StrategyTemplateId
from thytrader.strategies.template_parts import _adx, _atr, _draft, _template_name

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.market_data.models import DatasetTimeframe
    from thytrader.strategies.models import Instrument


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
