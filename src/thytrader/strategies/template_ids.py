"""Template identities, the agent-visible template catalog, and id parsing.

``StrategyTemplateId`` names every fail-closed draft template; ``template_catalog``
describes them for agents and the browser, and ``parse_template_id`` rejects unknown ids.
"""

from __future__ import annotations

from enum import StrEnum


class StrategyTemplateId(StrEnum):
    """Fail-closed draft templates. ema-trend is the historical reference."""

    EMA_TREND = "ema-trend"
    RSI_MEAN_REVERSION = "rsi-mean-reversion"
    MACD_TREND = "macd-trend"
    BOLLINGER_MEAN_REVERSION = "bollinger-mean-reversion"
    DONCHIAN_BREAKOUT = "donchian-breakout"
    SUPERTREND_TREND = "supertrend-trend"
    SQUEEZE_BREAKOUT = "squeeze-breakout"
    ZSCORE_MEAN_REVERSION = "zscore-mean-reversion"
    EMA_TREND_HOLD = "ema-trend-hold"
    BTC_REGIME_GATE = "btc-regime-gate"


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
        {
            "id": StrategyTemplateId.DONCHIAN_BREAKOUT.value,
            "name": "Donchian breakout",
            "description": (
                "Long when close crosses above the prior 20-bar Donchian high (offset 1); "
                "ATR stop and reward/risk target."
            ),
        },
        {
            "id": StrategyTemplateId.SUPERTREND_TREND.value,
            "name": "Supertrend trend",
            "description": (
                "Long when Supertrend(10, 3) flips to an uptrend and ADX(14) is at least 20; "
                "ATR stop and reward/risk target."
            ),
        },
        {
            "id": StrategyTemplateId.SQUEEZE_BREAKOUT.value,
            "name": "Squeeze breakout",
            "description": (
                "Long when close crosses above the upper Bollinger band right after the bands "
                "sat inside the Keltner channel (prior-bar squeeze); ATR stop and target."
            ),
        },
        {
            "id": StrategyTemplateId.ZSCORE_MEAN_REVERSION.value,
            "name": "Z-score mean reversion",
            "description": (
                "Long when the 20-bar close z-score is at or below -2 in a ranging regime "
                "(ADX below 20 or Choppiness above 61.8); ATR stop and target."
            ),
        },
        {
            "id": StrategyTemplateId.EMA_TREND_HOLD.value,
            "name": "EMA trend hold",
            "description": (
                "Long when EMA(20) crosses above EMA(100); hold until it crosses back below "
                "(signal exit). 3x ATR initial stop, no take-profit, wide 5x ATR trail."
            ),
        },
        {
            "id": StrategyTemplateId.BTC_REGIME_GATE.value,
            "name": "BTC regime gate",
            "description": (
                "Long when EMA(20) crosses above EMA(50), only while BTC's last closed daily "
                "close is above its EMA(100) (BTC is a read-only reference instrument). "
                "ATR stop and target."
            ),
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
