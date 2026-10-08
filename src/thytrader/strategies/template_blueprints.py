"""Template blueprints: indicator ids, defaults, and sweepable axes per template.

Discovery metadata for agents authoring parameter sweeps over a template; it grants no
trading authority and does not build documents (see ``templates``).
"""

from __future__ import annotations

from typing import Any

from thytrader.strategies.template_ids import StrategyTemplateId


def template_blueprint(template_id: StrategyTemplateId) -> dict[str, Any]:
    """Return one template's indicator ids, defaults, and sweepable axes.

    This is discovery metadata for agents authoring sweeps: it names the
    indicator ids a ``parameter_axes`` entry may reference and the exit/sizing
    defaults the template ships, without granting any trading authority.
    """
    blueprints: dict[StrategyTemplateId, dict[str, Any]] = {
        StrategyTemplateId.EMA_TREND: {
            "warmup_bars": 50,
            "indicator_ids": ("fast", "slow", "rsi", "atr"),
            "defaults": {
                "fast.period": "20",
                "slow.period": "50",
                "rsi.period": "14",
                "atr.period": "14",
                "entry.literal.rsi_gte": "50",
                "sizing.risk_fraction": "0.005",
                "exits.initial_stop_multiple": "2",
                "exits.take_profit_multiple": "2",
                "exits.max_bars_held": "96",
            },
            "sweepable_axes": (
                {"indicator_id": "fast", "parameter": "period", "range": [2, 500]},
                {"indicator_id": "slow", "parameter": "period", "range": [2, 500]},
                {"indicator_id": "rsi", "parameter": "period", "range": [2, 100]},
                {"target": "exits", "parameter": "initial_stop_multiple"},
                {"target": "exits", "parameter": "take_profit_multiple"},
                {"target": "exits", "parameter": "max_bars_held"},
                {"target": "sizing", "parameter": "risk_fraction"},
                {"target": "entry_literal", "indicator_id": "rsi", "parameter": "literal"},
            ),
        },
        StrategyTemplateId.RSI_MEAN_REVERSION: {
            "warmup_bars": 15,
            "indicator_ids": ("rsi", "atr"),
            "defaults": {
                "rsi.period": "14",
                "entry.literal.rsi_lte": "30",
                "sizing.risk_fraction": "0.005",
                "exits.initial_stop_multiple": "2",
                "exits.take_profit_multiple": "2",
                "exits.max_bars_held": "96",
            },
            "sweepable_axes": (
                {"indicator_id": "rsi", "parameter": "period", "range": [2, 100]},
                {"target": "exits", "parameter": "initial_stop_multiple"},
                {"target": "exits", "parameter": "take_profit_multiple"},
                {"target": "exits", "parameter": "max_bars_held"},
                {"target": "sizing", "parameter": "risk_fraction"},
                {"target": "entry_literal", "indicator_id": "rsi", "parameter": "literal"},
            ),
        },
        StrategyTemplateId.MACD_TREND: {
            "warmup_bars": 34,
            "indicator_ids": ("macd", "atr"),
            "defaults": {
                "macd.fast_period": "12",
                "macd.slow_period": "26",
                "macd.signal_period": "9",
                "atr.period": "14",
                "sizing.risk_fraction": "0.005",
                "exits.initial_stop_multiple": "2",
                "exits.take_profit_multiple": "2",
                "exits.max_bars_held": "96",
            },
            "sweepable_axes": (
                {"indicator_id": "macd", "parameter": "fast_period", "range": [2, 500]},
                {"indicator_id": "macd", "parameter": "slow_period", "range": [2, 500]},
                {"indicator_id": "macd", "parameter": "signal_period", "range": [2, 500]},
                {"target": "exits", "parameter": "initial_stop_multiple"},
                {"target": "exits", "parameter": "take_profit_multiple"},
                {"target": "exits", "parameter": "max_bars_held"},
                {"target": "sizing", "parameter": "risk_fraction"},
            ),
        },
        StrategyTemplateId.BOLLINGER_MEAN_REVERSION: {
            "warmup_bars": 20,
            "indicator_ids": ("close", "bands", "atr"),
            "defaults": {
                "bands.period": "20",
                "bands.stdev_multiplier": "2",
                "atr.period": "14",
                "sizing.risk_fraction": "0.005",
                "exits.initial_stop_multiple": "2",
                "exits.take_profit_multiple": "2",
                "exits.max_bars_held": "96",
            },
            "sweepable_axes": (
                {"indicator_id": "bands", "parameter": "period", "range": [2, 500]},
                {"indicator_id": "bands", "parameter": "stdev_multiplier"},
                {"target": "exits", "parameter": "initial_stop_multiple"},
                {"target": "exits", "parameter": "take_profit_multiple"},
                {"target": "exits", "parameter": "max_bars_held"},
                {"target": "sizing", "parameter": "risk_fraction"},
            ),
        },
        **_catalog_template_blueprints(),
    }
    blueprint = dict(blueprints[template_id])
    blueprint["id"] = template_id.value
    return blueprint


_SHARED_DEFAULTS: dict[str, str] = {
    "atr.period": "14",
    "sizing.risk_fraction": "0.005",
    "exits.initial_stop_multiple": "2",
    "exits.take_profit_multiple": "2",
    "exits.max_bars_held": "96",
}


_SHARED_AXES: tuple[dict[str, object], ...] = (
    {"target": "exits", "parameter": "initial_stop_multiple"},
    {"target": "exits", "parameter": "take_profit_multiple"},
    {"target": "exits", "parameter": "max_bars_held"},
    {"target": "sizing", "parameter": "risk_fraction"},
)


def _catalog_template_blueprints() -> dict[StrategyTemplateId, dict[str, Any]]:
    """Return blueprints for the templates built on the wider indicator catalog."""
    return {
        StrategyTemplateId.DONCHIAN_BREAKOUT: {
            "warmup_bars": 21,
            "indicator_ids": ("close", "channel", "atr"),
            "defaults": {"channel.period": "20", "channel.offset": "1", **_SHARED_DEFAULTS},
            "sweepable_axes": (
                {"indicator_id": "channel", "parameter": "period", "range": [2, 500]},
                {"indicator_id": "channel", "parameter": "offset", "range": [1, 500]},
                *_SHARED_AXES,
            ),
        },
        StrategyTemplateId.SUPERTREND_TREND: {
            "warmup_bars": 27,
            "indicator_ids": ("trend", "zero", "adx", "atr"),
            "defaults": {
                "trend.period": "10",
                "trend.multiplier": "3",
                "adx.period": "14",
                "entry.literal.adx_gte": "20",
                **_SHARED_DEFAULTS,
            },
            "sweepable_axes": (
                {"indicator_id": "trend", "parameter": "period", "range": [2, 100]},
                {"indicator_id": "trend", "parameter": "multiplier"},
                {"target": "entry_literal", "indicator_id": "adx", "parameter": "literal"},
                *_SHARED_AXES,
            ),
        },
        StrategyTemplateId.SQUEEZE_BREAKOUT: {
            "warmup_bars": 21,
            "indicator_ids": ("close", "bands", "channel", "atr"),
            "defaults": {
                "bands.period": "20",
                "bands.stdev_multiplier": "2",
                "channel.period": "20",
                "channel.atr_period": "10",
                "channel.multiplier": "1.5",
                **_SHARED_DEFAULTS,
            },
            "sweepable_axes": (
                {"indicator_id": "bands", "parameter": "period", "range": [2, 500]},
                {"indicator_id": "bands", "parameter": "stdev_multiplier"},
                {"indicator_id": "channel", "parameter": "period", "range": [2, 500]},
                {"indicator_id": "channel", "parameter": "atr_period", "range": [2, 100]},
                {"indicator_id": "channel", "parameter": "multiplier"},
                *_SHARED_AXES,
            ),
        },
        StrategyTemplateId.EMA_TREND_HOLD: {
            "warmup_bars": 100,
            "indicator_ids": ("fast", "slow", "atr"),
            "defaults": {
                "fast.period": "20",
                "slow.period": "100",
                "atr.period": "14",
                "sizing.risk_fraction": "0.005",
                "exits.initial_stop_multiple": "3",
                "exits.take_profit": "none",
                "exits.trailing_stop_multiple": "5",
                "exits.max_bars_held": "1000",
                "exits.signal_exit": "fast crosses below slow",
            },
            # The exit rule references the same fast/slow ids as the entry, so a period
            # axis moves both rules together.
            "sweepable_axes": (
                {"indicator_id": "fast", "parameter": "period", "range": [2, 500]},
                {"indicator_id": "slow", "parameter": "period", "range": [2, 500]},
                {"target": "exits", "parameter": "initial_stop_multiple"},
                {"target": "exits", "parameter": "trailing_stop_multiple"},
                {"target": "exits", "parameter": "max_bars_held"},
                {"target": "sizing", "parameter": "risk_fraction"},
            ),
        },
        StrategyTemplateId.ZSCORE_MEAN_REVERSION: {
            "warmup_bars": 27,
            "indicator_ids": ("z", "adx", "chop", "atr"),
            "defaults": {
                "z.period": "20",
                "adx.period": "14",
                "chop.period": "14",
                "entry.literal.z_lte": "-2",
                "entry.literal.adx_lt": "20",
                "entry.literal.chop_gt": "61.8",
                **_SHARED_DEFAULTS,
            },
            "sweepable_axes": (
                {"indicator_id": "z", "parameter": "period", "range": [2, 500]},
                {"target": "entry_literal", "indicator_id": "z", "parameter": "literal"},
                {"target": "entry_literal", "indicator_id": "adx", "parameter": "literal"},
                {"target": "entry_literal", "indicator_id": "chop", "parameter": "literal"},
                *_SHARED_AXES,
            ),
        },
        StrategyTemplateId.BTC_REGIME_GATE: _BTC_REGIME_BLUEPRINT,
    }


_BTC_REGIME_BLUEPRINT: dict[str, Any] = {
    "warmup_bars": 50,
    "indicator_ids": ("fast", "slow", "atr", "btc_close", "btc_ema"),
    "reference_instruments": (
        {"id": "btc", "product_id": "BTC-<instrument quote>", "timeframe": "1d"},
    ),
    "defaults": {
        "fast.period": "20",
        "slow.period": "50",
        "atr.period": "14",
        "btc_ema.period": "100",
        "entry.reference_gate": "btc_close > btc_ema (BTC 1d, last closed bar)",
        **_SHARED_DEFAULTS,
    },
    # btc_close and btc_ema read the BTC reference instrument (source "btc"); their warmup
    # is derived on the reference's 1d clock, so a btc_ema period axis needs no warmup edit.
    "sweepable_axes": (
        {"indicator_id": "fast", "parameter": "period", "range": [2, 500]},
        {"indicator_id": "slow", "parameter": "period", "range": [2, 500]},
        {"indicator_id": "btc_ema", "parameter": "period", "range": [2, 500]},
        *_SHARED_AXES,
    ),
}
