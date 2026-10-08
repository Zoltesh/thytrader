"""Breakout template builders: Donchian channel and Bollinger-in-Keltner squeeze."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.strategies.models import (
    AllCondition,
    ComparisonCondition,
    ComparisonOperator,
    IndicatorDefinition,
    IndicatorKind,
    IndicatorOperand,
    IndicatorParameters,
    StrategyDefinition,
)
from thytrader.strategies.template_ids import StrategyTemplateId
from thytrader.strategies.template_parts import (
    _atr,
    _bands,
    _close,
    _draft,
    _keltner,
    _template_name,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.market_data.models import DatasetTimeframe
    from thytrader.strategies.models import Instrument


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
            _atr(),
        ),
        when=AllCondition(
            all=(
                ComparisonCondition(
                    left=IndicatorOperand(indicator="bands", series="upper", offset=1),
                    operator=ComparisonOperator.LT,
                    right=IndicatorOperand(indicator="channel", series="upper", offset=1),
                ),
                ComparisonCondition(
                    left=IndicatorOperand(indicator="bands", series="lower", offset=1),
                    operator=ComparisonOperator.GT,
                    right=IndicatorOperand(indicator="channel", series="lower", offset=1),
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
