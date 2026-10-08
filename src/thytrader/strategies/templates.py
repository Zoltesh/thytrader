"""Template registry: build a validated draft strategy from a named template.

Maps every ``StrategyTemplateId`` to its builder; the trend, mean-reversion, and breakout
families live in ``template_trend``, ``template_mean_reversion``, and ``template_breakout``.
Discovery metadata lives in ``template_ids`` (identities and catalog) and
``template_blueprints`` (sweep axes).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.strategies.template_breakout import _donchian_breakout, _squeeze_breakout
from thytrader.strategies.template_ids import StrategyTemplateId
from thytrader.strategies.template_mean_reversion import (
    _bollinger_mean_reversion,
    _rsi_mean_reversion,
    _zscore_mean_reversion,
)
from thytrader.strategies.template_trend import (
    _btc_regime_gate,
    _ema_trend,
    _ema_trend_hold,
    _macd_trend,
    _supertrend_trend,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from uuid import UUID

    from thytrader.market_data.models import DatasetTimeframe
    from thytrader.strategies.models import (
        Instrument,
        StrategyDefinition,
    )


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
    StrategyTemplateId.EMA_TREND_HOLD: _ema_trend_hold,
    StrategyTemplateId.BTC_REGIME_GATE: _btc_regime_gate,
}
