"""Human-readable strategy summaries: quote-aware notional, groups, new kinds, and lags."""

from __future__ import annotations

from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.summary import strategy_summary


def test_notional_range_names_the_instrument_quote_currency() -> None:
    """USDC strategies read '10-100 USDC'; no dollar sign is assumed."""
    usdc = strategy_summary(create_template_strategy(product_id="ETH-USDC"))
    usd = strategy_summary(create_template_strategy(product_id="ETH-USD"))
    assert usdc.endswith("10-100 USDC")
    assert usd.endswith("10-100 USD")
    assert "$" not in usdc


def test_historical_kinds_keep_their_wording() -> None:
    """Existing EMA/RSI/MACD/Bollinger phrasing is unchanged."""
    assert "EMA(20) crosses above EMA(50) AND RSI(14) ≥ 50" in strategy_summary(
        create_template_strategy()
    )
    assert "MACD line crosses above MACD signal" in strategy_summary(
        create_template_strategy(template="macd-trend")
    )
    assert "close ≤ lower Bollinger band" in strategy_summary(
        create_template_strategy(template="bollinger-mean-reversion")
    )


def test_new_kinds_render_label_parameters_series_and_lag() -> None:
    """Catalog kinds read as Label(parameters) series, with '(N bars ago)' for offsets."""
    donchian = strategy_summary(create_template_strategy(template="donchian-breakout"))
    assert "close crosses above Donchian(20) upper (1 bar ago)" in donchian
    supertrend = strategy_summary(create_template_strategy(template="supertrend-trend"))
    assert "Supertrend(10, 3) direction crosses above zero" in supertrend


def test_nested_groups_with_a_different_joiner_are_parenthesized() -> None:
    """A AND (B OR C) never flattens into the ambiguous A AND B OR C."""
    summary = strategy_summary(create_template_strategy(template="zscore-mean-reversion"))
    assert "Z-score(20) ≤ -2 AND (ADX(14) < 20 OR Choppiness(14) > 61.8)" in summary
