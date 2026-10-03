"""Human-readable strategy summaries: quote-aware notional, groups, new kinds, and lags."""

from __future__ import annotations

import pytest

from tests.strategies.reference_support import reference_strategy
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import StrategyDefinition
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


def test_squeeze_summary_distinguishes_prior_bands_from_current_breakout() -> None:
    """Both prior-bar comparisons name their lag while the breakout reads current bands."""
    summary = strategy_summary(create_template_strategy(template="squeeze-breakout"))
    assert "upper Bollinger band (1 bar ago) < Keltner(20, 10, 1.5) upper (1 bar ago)" in summary
    assert "lower Bollinger band (1 bar ago) > Keltner(20, 10, 1.5) lower (1 bar ago)" in summary
    assert "close crosses above upper Bollinger band ·" in summary


@pytest.mark.parametrize(
    ("declaration_offset", "operand_offset", "label"),
    [
        (None, 0, "EMA(20)"),
        (2, None, "EMA(20) (2 bars ago)"),
        (None, 1, "EMA(20) (1 bar ago)"),
        (2, 3, "EMA(20) (5 bars ago)"),
    ],
)
def test_summary_names_the_effective_lag_of_each_operand(
    declaration_offset: int | None, operand_offset: int | None, label: str
) -> None:
    """The left read combines native-clock lags without changing the right read's label."""
    payload = create_template_strategy().model_dump(mode="json", by_alias=True)
    payload["indicators"][0]["offset"] = declaration_offset
    payload["entry"]["when"]["all"][0]["left"]["offset"] = operand_offset
    summary = strategy_summary(StrategyDefinition.model_validate(payload))
    assert f"{label} crosses above EMA(50) AND" in summary


def test_signal_exit_summary_includes_the_right_operands_lag() -> None:
    """An exit comparing current fast EMA to prior slow EMA exposes the distinct reads."""
    payload = create_template_strategy().model_dump(mode="json", by_alias=True)
    payload["data_requirements"]["warmup_bars"] = 52
    payload["exits"]["signal_exit"] = {
        "when": {
            "all": [
                {
                    "left": {"indicator": "fast"},
                    "operator": "less_than",
                    "right": {"indicator": "slow", "offset": 2},
                }
            ]
        }
    }
    summary = strategy_summary(StrategyDefinition.model_validate(payload))
    assert "exit when EMA(20) < EMA(50) (2 bars ago) ·" in summary


def test_reference_operand_summary_keeps_the_native_clock_with_combined_lag() -> None:
    """A lagged daily BTC read in an hourly ETH strategy retains its reference clock."""
    payload = reference_strategy().model_dump(mode="json", by_alias=True)
    payload["indicators"][2]["offset"] = 1
    payload["entry"]["when"]["all"][0]["right"]["offset"] = 2
    summary = strategy_summary(StrategyDefinition.model_validate(payload))
    assert "BTC · close [1d] > BTC · SMA(2) (3 bars ago) [1d]" in summary
