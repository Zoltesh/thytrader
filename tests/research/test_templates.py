"""Tests for Phase 11 research draft templates."""

from __future__ import annotations

import pytest

from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import (
    AllCondition,
    ComparisonCondition,
    IndicatorKind,
    IndicatorOperand,
    LiteralOperand,
    decision_clock_indicators,
    extra_indicator_timeframe_warmup,
    strategy_indicator_operands,
)
from thytrader.strategies.template_blueprints import template_blueprint
from thytrader.strategies.template_ids import (
    StrategyTemplateId,
    parse_template_id,
    template_catalog,
)


def test_default_template_matches_historical_ema_reference() -> None:
    """ema-trend keeps the BTC 1h name and EMA crossover entry."""
    draft = create_template_strategy()
    assert draft.name == "BTC hourly EMA trend"
    kinds = {indicator.id: indicator.kind for indicator in draft.indicators}
    assert kinds["fast"] is IndicatorKind.EMA
    assert kinds["slow"] is IndicatorKind.EMA
    assert draft.metadata.tags == ("reference",)


def test_rsi_mean_reversion_template_uses_oversold_threshold() -> None:
    """RSI template is long-only with an RSI LTE 30 leaf."""
    draft = create_template_strategy(template="rsi-mean-reversion", product_id="ETH-USD")
    assert "RSI mean reversion" in draft.name
    assert draft.instrument.product_id == "ETH-USD"
    assert isinstance(draft.entry.when, AllCondition)
    condition = draft.entry.when.all[0]
    assert isinstance(condition, ComparisonCondition)
    assert isinstance(condition.right, LiteralOperand)
    assert condition.right.literal == "30"


def test_macd_and_bollinger_templates_require_series_operands() -> None:
    """Multi-series templates name MACD/Bollinger series ids on conditions."""
    macd = create_template_strategy(template="macd-trend")
    bollinger = create_template_strategy(template="bollinger-mean-reversion")
    assert isinstance(macd.entry.when, AllCondition)
    macd_when = macd.entry.when.all[0]
    assert isinstance(macd_when, ComparisonCondition)
    assert isinstance(macd_when.left, IndicatorOperand)
    assert isinstance(macd_when.right, IndicatorOperand)
    assert macd_when.left.series == "macd"
    assert macd_when.right.series == "signal"
    assert isinstance(bollinger.entry.when, AllCondition)
    bollinger_when = bollinger.entry.when.all[0]
    assert isinstance(bollinger_when, ComparisonCondition)
    assert isinstance(bollinger_when.right, IndicatorOperand)
    assert bollinger_when.right.series == "lower"


def test_unknown_template_is_rejected() -> None:
    """Fail closed instead of inventing an unpublished template."""
    with pytest.raises(ValueError, match="Unknown strategy template"):
        parse_template_id("stochastic")
    ids = {item["id"] for item in template_catalog()}
    assert ids == {item.value for item in StrategyTemplateId}


@pytest.mark.parametrize("template", list(StrategyTemplateId), ids=lambda item: item.value)
def test_blueprints_describe_the_built_document(template: StrategyTemplateId) -> None:
    """Blueprint warmup, ids, and axes match what create-strategy actually builds."""
    draft = create_template_strategy(template=template.value, product_id="ETH-USDC")
    blueprint = template_blueprint(template)
    declared = {indicator.id for indicator in draft.indicators}
    assert blueprint["warmup_bars"] == draft.data_requirements.warmup_bars
    assert blueprint["warmup_bars"] == extra_indicator_timeframe_warmup(
        decision_clock_indicators(draft), operands=strategy_indicator_operands(draft)
    )
    assert set(blueprint["indicator_ids"]) == declared
    for key in blueprint["defaults"]:
        prefix = key.split(".", 1)[0]
        assert prefix in declared | {"entry", "sizing", "exits"}, key
    for axis in blueprint["sweepable_axes"]:
        indicator_id = axis.get("indicator_id")
        assert indicator_id is None or indicator_id in declared, axis


def test_catalog_templates_use_the_wider_indicator_catalog() -> None:
    """The new templates reference new kinds, series ids, and prior-bar offsets."""
    donchian = create_template_strategy(template="donchian-breakout")
    channel = next(item for item in donchian.indicators if item.id == "channel")
    assert channel.kind is IndicatorKind.DONCHIAN
    assert channel.offset == 1
    supertrend = create_template_strategy(template="supertrend-trend")
    assert {item.kind for item in supertrend.indicators} >= {
        IndicatorKind.SUPERTREND,
        IndicatorKind.CONSTANT,
        IndicatorKind.ADX,
    }
    squeeze = create_template_strategy(template="squeeze-breakout")
    lagged = {item.indicator for item in strategy_indicator_operands(squeeze) if item.offset == 1}
    assert lagged == {"bands", "channel"}
    zscore = create_template_strategy(template="zscore-mean-reversion", timeframe="4h")
    assert zscore.timeframe == "4h"
    assert {item.kind for item in zscore.indicators} >= {
        IndicatorKind.ZSCORE,
        IndicatorKind.CHOPPINESS,
    }
    assert "Z-score mean reversion" in zscore.name
