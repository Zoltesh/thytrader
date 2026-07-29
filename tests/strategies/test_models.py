"""Tests for the first canonical strategy-schema publication profile."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import ValidationError
import pytest

from thytrader.strategies.models import (
    StrategyDefinition,
    StrategyStatus,
    canonical_strategy_bytes,
    strategy_fingerprint,
)


def _object_mapping(value: object) -> dict[str, object]:
    """Narrow one mutable JSON object used by an adversarial test fixture."""
    if not isinstance(value, dict):
        raise TypeError("Expected a mutable JSON object.")
    return cast("dict[str, object]", value)


def _object_list(value: object) -> list[object]:
    """Narrow one mutable JSON array used by an adversarial test fixture."""
    if not isinstance(value, list):
        raise TypeError("Expected a mutable JSON array.")
    return cast("list[object]", value)


def reference_payload() -> dict[str, object]:
    """Return the documented conservative BTC-USD reference profile."""
    return {
        "schema_version": "1.0",
        "strategy_id": "01985cf0-7b60-7000-8000-000000000001",
        "version": 1,
        "name": "BTC hourly EMA trend",
        "description": "Reference research strategy; not trading authority.",
        "status": "published",
        "created_at": "2026-07-29T18:00:00Z",
        "instrument": {
            "product_id": "BTC-USD",
            "base_currency": "BTC",
            "quote_currency": "USD",
        },
        "timeframe": "1h",
        "data_requirements": {
            "warmup_bars": 50,
            "required_fields": ["open", "high", "low", "close", "volume"],
        },
        "indicators": [
            {"id": "ema_fast", "kind": "ema", "input": "close", "parameters": {"period": 20}},
            {"id": "ema_slow", "kind": "ema", "input": "close", "parameters": {"period": 50}},
            {"id": "rsi", "kind": "rsi", "input": "close", "parameters": {"period": 14}},
            {
                "id": "atr",
                "kind": "atr",
                "input": ["high", "low", "close"],
                "parameters": {"period": 14},
            },
        ],
        "entry": {
            "side": "long",
            "when": {
                "all": [
                    {
                        "left": {"indicator": "ema_fast"},
                        "operator": "crosses_above",
                        "right": {"indicator": "ema_slow"},
                    },
                    {
                        "left": {"indicator": "rsi"},
                        "operator": "greater_than",
                        "right": {"literal": "50"},
                    },
                ]
            },
            "cooldown_bars": 3,
            "max_open_positions": 1,
        },
        "sizing": {
            "kind": "risk_fraction",
            "risk_fraction": "0.005",
            "min_quote_notional": "10",
            "max_quote_notional": "100",
        },
        "portfolio_limits": {
            "max_strategy_exposure_fraction": "0.10",
            "max_concurrent_positions": 1,
        },
        "exits": {
            "initial_stop": {
                "kind": "atr_multiple",
                "atr_indicator": "atr",
                "multiple": "2.0",
            },
            "take_profit": {"kind": "reward_risk", "multiple": "2.0"},
            "trailing_stop": {"enabled": False},
            "time_exit": {"max_bars_held": 96},
        },
        "execution": {
            "entry_preference": "maker_only",
            "max_entry_wait_bars": 2,
            "on_unfilled_entry": "cancel",
        },
        "metadata": {"tags": ["reference"], "notes": ["Research use only."]},
    }


def test_reference_strategy_has_stable_canonical_fingerprint() -> None:
    """Validated definitions serialize deterministically and hash the whole document."""
    definition = StrategyDefinition.model_validate(reference_payload())
    canonical = canonical_strategy_bytes(definition)

    assert definition.status is StrategyStatus.PUBLISHED
    assert definition.created_at == datetime(2026, 7, 29, 18, tzinfo=UTC)
    assert canonical == (Path(__file__).parent / "golden/reference_strategy_v1.json").read_bytes()
    assert strategy_fingerprint(definition) == (
        "sha256:9109f4a024c595ee769a5886a0f147208e2a01c86c26e34aec08dfccdf0f4ea3"
    )
    assert strategy_fingerprint(definition) == strategy_fingerprint(
        StrategyDefinition.model_validate_json(canonical)
    )
    without_description = reference_payload()
    del without_description["description"]
    assert StrategyDefinition.model_validate(without_description).description is None

    equivalent_decimal = reference_payload()
    sizing = _object_mapping(equivalent_decimal["sizing"])
    sizing["risk_fraction"] = "0.0050"
    equivalent_definition = StrategyDefinition.model_validate(equivalent_decimal)
    assert strategy_fingerprint(equivalent_definition) == strategy_fingerprint(definition)

    precise_decimal = reference_payload()
    precise_sizing = _object_mapping(precise_decimal["sizing"])
    precise_sizing["risk_fraction"] = "0.12345678901234567890123456789"
    precise_definition = StrategyDefinition.model_validate(precise_decimal)
    assert precise_definition.sizing.risk_fraction == "0.12345678901234567890123456789"

    equivalent_precise_decimal = reference_payload()
    equivalent_precise_sizing = _object_mapping(equivalent_precise_decimal["sizing"])
    equivalent_precise_sizing["risk_fraction"] = "0.123456789012345678901234567890"
    equivalent_precise_definition = StrategyDefinition.model_validate(equivalent_precise_decimal)
    assert equivalent_precise_definition == precise_definition

    distinct_precise_decimal = reference_payload()
    distinct_precise_sizing = _object_mapping(distinct_precise_decimal["sizing"])
    distinct_precise_sizing["risk_fraction"] = "0.12345678901234567890123456790"
    distinct_precise_definition = StrategyDefinition.model_validate(distinct_precise_decimal)
    assert distinct_precise_definition.sizing.risk_fraction == "0.1234567890123456789012345679"
    assert strategy_fingerprint(distinct_precise_definition) != strategy_fingerprint(
        precise_definition
    )


def test_strategy_rejects_unknown_fields_floats_and_unresolved_indicators() -> None:
    """Structural and semantic validation fails closed at the strategy boundary."""
    unknown = reference_payload()
    unknown["executable_python"] = "buy()"
    with pytest.raises(ValidationError):
        StrategyDefinition.model_validate(unknown)

    floating = reference_payload()
    floating["sizing"] = {
        "kind": "risk_fraction",
        "risk_fraction": 0.005,
        "min_quote_notional": "10",
        "max_quote_notional": "100",
    }
    with pytest.raises(ValidationError):
        StrategyDefinition.model_validate(floating)

    oversized_risk = reference_payload()
    oversized_risk["sizing"] = {
        "kind": "risk_fraction",
        "risk_fraction": "0.26",
        "min_quote_notional": "10",
        "max_quote_notional": "100",
    }
    with pytest.raises(ValidationError, match=r"at most 0\.25"):
        StrategyDefinition.model_validate(oversized_risk)

    unresolved = reference_payload()
    unresolved["entry"] = {
        "side": "long",
        "when": {
            "all": [
                {
                    "left": {"indicator": "missing"},
                    "operator": "greater_than",
                    "right": {"literal": "1"},
                }
            ]
        },
        "cooldown_bars": 3,
        "max_open_positions": 1,
    }
    with pytest.raises(ValidationError, match="unknown indicator"):
        StrategyDefinition.model_validate(unresolved)


def test_strategy_rejects_duplicate_indicators_and_insufficient_warmup() -> None:
    """Indicator identity and warmup requirements are checked semantically."""
    duplicate = reference_payload()
    indicators = duplicate["indicators"]
    assert isinstance(indicators, list)
    duplicate["indicators"] = [indicators[0], indicators[0], *indicators[2:]]
    with pytest.raises(ValidationError, match="indicator ids"):
        StrategyDefinition.model_validate(duplicate)

    insufficient = reference_payload()
    insufficient["data_requirements"] = {
        "warmup_bars": 49,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    with pytest.raises(ValidationError, match="warmup"):
        StrategyDefinition.model_validate(insufficient)

    missing_atr_fields = reference_payload()
    missing_atr_fields["data_requirements"] = {
        "warmup_bars": 50,
        "required_fields": ["close", "volume"],
    }
    with pytest.raises(ValidationError, match="required_fields"):
        StrategyDefinition.model_validate(missing_atr_fields)

    invalid_ema_period = reference_payload()
    ema_indicators = _object_list(invalid_ema_period["indicators"])
    _object_mapping(ema_indicators[0])["parameters"] = {"period": 1}
    with pytest.raises(ValidationError, match="greater than or equal to 2"):
        StrategyDefinition.model_validate(invalid_ema_period)

    invalid_atr_period = reference_payload()
    atr_indicators = _object_list(invalid_atr_period["indicators"])
    _object_mapping(atr_indicators[3])["parameters"] = {"period": 101}
    invalid_atr_period["data_requirements"] = {
        "warmup_bars": 101,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    with pytest.raises(ValidationError, match="ATR period"):
        StrategyDefinition.model_validate(invalid_atr_period)

    maximum_atr_period = reference_payload()
    maximum_atr_indicators = _object_list(maximum_atr_period["indicators"])
    _object_mapping(maximum_atr_indicators[3])["parameters"] = {"period": 100}
    maximum_atr_period["data_requirements"] = {
        "warmup_bars": 100,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    StrategyDefinition.model_validate(maximum_atr_period)
