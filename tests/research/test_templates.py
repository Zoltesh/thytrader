"""Tests for Phase 11 research draft templates."""

from __future__ import annotations

import pytest

from thytrader.strategies.authoring import create_reference_draft
from thytrader.strategies.models import (
    AllCondition,
    ComparisonCondition,
    IndicatorKind,
    IndicatorOperand,
    LiteralOperand,
)
from thytrader.strategies.templates import StrategyTemplateId, parse_template_id, template_catalog


def test_default_template_matches_historical_ema_reference() -> None:
    """ema-trend keeps the BTC 1h name and EMA crossover entry."""
    draft = create_reference_draft()
    assert draft.name == "BTC hourly EMA trend"
    kinds = {indicator.id: indicator.kind for indicator in draft.indicators}
    assert kinds["fast"] is IndicatorKind.EMA
    assert kinds["slow"] is IndicatorKind.EMA
    assert draft.metadata.tags == ("reference",)


def test_rsi_mean_reversion_template_uses_oversold_threshold() -> None:
    """RSI template is long-only with an RSI LTE 30 leaf."""
    draft = create_reference_draft(template="rsi-mean-reversion", product_id="ETH-USD")
    assert "RSI mean reversion" in draft.name
    assert draft.instrument.product_id == "ETH-USD"
    assert isinstance(draft.entry.when, AllCondition)
    condition = draft.entry.when.all[0]
    assert isinstance(condition, ComparisonCondition)
    assert isinstance(condition.right, LiteralOperand)
    assert condition.right.literal == "30"


def test_macd_and_bollinger_templates_require_series_operands() -> None:
    """Multi-series templates name MACD/Bollinger series ids on conditions."""
    macd = create_reference_draft(template="macd-trend")
    bollinger = create_reference_draft(template="bollinger-mean-reversion")
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
