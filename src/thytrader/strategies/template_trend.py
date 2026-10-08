"""Trend-following template builders.

EMA trend (the historical reference), EMA trend hold, the BTC regime gate, MACD trend,
and Supertrend trend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.strategies.models import (
    AllCondition,
    AtrMultipleStop,
    AtrTrailingStop,
    ComparisonCondition,
    ComparisonOperator,
    ConstantIndicatorParameters,
    EmptyIndicatorParameters,
    ExitDefinition,
    IndicatorDefinition,
    IndicatorKind,
    IndicatorOperand,
    IndicatorParameters,
    LiteralOperand,
    MacdIndicatorParameters,
    NoTakeProfit,
    ReferenceInstrument,
    SignalExit,
    StrategyDefinition,
    SupertrendIndicatorParameters,
    TimeExit,
)
from thytrader.strategies.template_ids import StrategyTemplateId
from thytrader.strategies.template_parts import _adx, _atr, _draft, _template_name

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.market_data.models import DatasetTimeframe
    from thytrader.strategies.models import Instrument


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


def _ema_trend_hold(
    strategy_id: UUID,
    created_at: datetime,
    instrument: Instrument,
    timeframe: DatasetTimeframe,
) -> StrategyDefinition:
    """Hold while EMA(20) stays above EMA(100): enter on the cross up, exit on the cross down.

    The signal exit (ADR 0093) is the primary exit. The 3x ATR initial stop stays
    mandatory, there is no take-profit, and a wide 5x ATR trail only guards against a
    crash faster than the slow EMA can turn; drop it with ``{"enabled": false}``.
    """
    return _draft(
        strategy_id=strategy_id,
        created_at=created_at,
        instrument=instrument,
        timeframe=timeframe,
        name=_template_name(instrument.product_id, timeframe, "EMA trend hold"),
        description=(
            "Long while EMA(20) holds above EMA(100); exits on the cross back below. "
            "Research template; not trading authority."
        ),
        warmup_bars=100,
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
                parameters=IndicatorParameters(period=100),
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
            )
        ),
        tags=("template", StrategyTemplateId.EMA_TREND_HOLD.value),
        exits=ExitDefinition(
            initial_stop=AtrMultipleStop(kind="atr_multiple", atr_indicator="atr", multiple="3"),
            take_profit=NoTakeProfit(kind="none"),
            trailing_stop=AtrTrailingStop(
                enabled=True, kind="atr_multiple", atr_indicator="atr", multiple="5"
            ),
            time_exit=TimeExit(max_bars_held=1000),
            signal_exit=SignalExit(
                when=AllCondition(
                    all=(
                        ComparisonCondition(
                            left=IndicatorOperand(indicator="fast"),
                            operator=ComparisonOperator.CROSSES_BELOW,
                            right=IndicatorOperand(indicator="slow"),
                        ),
                    )
                )
            ),
        ),
    )


def _btc_regime_gate(
    strategy_id: UUID,
    created_at: datetime,
    instrument: Instrument,
    timeframe: DatasetTimeframe,
) -> StrategyDefinition:
    """EMA(20/50) crossover gated by BTC's last closed daily close above its EMA(100).

    BTC is a read-only reference instrument (ADR 0096) in the instrument's quote
    currency: ``btc_close`` and ``btc_ema`` read ``BTC-<quote>`` 1d bars, and at each
    decision close only the last daily bar that has already closed is used. Orders are
    only ever placed on the traded instrument.
    """
    reference = ReferenceInstrument(
        id="btc", product_id=f"BTC-{instrument.quote_currency}", timeframe="1d"
    )
    return _draft(
        strategy_id=strategy_id,
        created_at=created_at,
        instrument=instrument,
        timeframe=timeframe,
        name=_template_name(instrument.product_id, timeframe, "BTC regime gate"),
        description=(
            "EMA(20/50) trend entries only while BTC's daily close is above its EMA(100). "
            "Research template; not trading authority."
        ),
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
            _atr(),
            IndicatorDefinition(
                id="btc_close",
                kind=IndicatorKind.IDENTITY,
                input="close",
                parameters=EmptyIndicatorParameters(),
                source=reference.id,
            ),
            IndicatorDefinition(
                id="btc_ema",
                kind=IndicatorKind.EMA,
                input="close",
                parameters=IndicatorParameters(period=100),
                source=reference.id,
            ),
        ),
        when=AllCondition(
            all=(
                ComparisonCondition(
                    left=IndicatorOperand(indicator="fast"),
                    operator=ComparisonOperator.CROSSES_ABOVE,
                    right=IndicatorOperand(indicator="slow"),
                ),
                ComparisonCondition(
                    left=IndicatorOperand(indicator="btc_close"),
                    operator=ComparisonOperator.GT,
                    right=IndicatorOperand(indicator="btc_ema"),
                ),
            )
        ),
        tags=("template", StrategyTemplateId.BTC_REGIME_GATE.value),
        reference_instruments=(reference,),
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
