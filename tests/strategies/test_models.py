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


def _ema_comparison_payload() -> dict[str, object]:
    """Return one valid comparison leaf for condition-complexity fixtures."""
    return {
        "left": {"indicator": "ema_fast"},
        "operator": "greater_than",
        "right": {"indicator": "ema_slow"},
    }


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


def sma_strategy_payload() -> dict[str, object]:
    """Return a deterministic published strategy containing a close-price SMA."""
    payload = reference_payload()
    payload["strategy_id"] = "01985cf0-7b60-7000-8000-000000000002"
    payload["name"] = "BTC hourly SMA profile"
    _object_list(payload["indicators"]).append(
        {"id": "sma_trend", "kind": "sma", "input": "close", "parameters": {"period": 30}}
    )
    return payload


def volume_sma_strategy_payload() -> dict[str, object]:
    """Return a deterministic published strategy containing a volume SMA."""
    payload = reference_payload()
    payload["strategy_id"] = "01985cf0-7b60-7000-8000-000000000003"
    payload["name"] = "BTC hourly volume SMA profile"
    _object_list(payload["indicators"]).append(
        {
            "id": "average_volume",
            "kind": "volume_sma",
            "input": "volume",
            "parameters": {"period": 30},
        }
    )
    return payload


def nested_condition_strategy_payload() -> dict[str, object]:
    """Return a deterministic strategy containing nested AND, OR, and NOT groups."""
    payload = reference_payload()
    payload["strategy_id"] = "01985cf0-7b60-7000-8000-000000000004"
    payload["name"] = "BTC hourly nested condition profile"
    entry = _object_mapping(payload["entry"])
    entry["when"] = {
        "all": [
            {
                "left": {"indicator": "ema_fast"},
                "operator": "crosses_above",
                "right": {"indicator": "ema_slow"},
            },
            {
                "any": [
                    {
                        "left": {"indicator": "rsi"},
                        "operator": "greater_than",
                        "right": {"literal": "50"},
                    },
                    {
                        "not": {
                            "left": {"indicator": "ema_fast"},
                            "operator": "less_than",
                            "right": {"indicator": "ema_slow"},
                        }
                    },
                ]
            },
        ]
    }
    return payload


def _assert_golden_strategy(
    payload: dict[str, object], filename: str, expected_fingerprint: str
) -> None:
    """Require one canonical strategy variant to match fixed bytes and identity."""
    definition = StrategyDefinition.model_validate(payload)
    expected_bytes = (Path(__file__).parent / "golden" / filename).read_bytes()
    assert canonical_strategy_bytes(definition) == expected_bytes
    assert strategy_fingerprint(definition) == expected_fingerprint


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


def test_sma_strategy_has_stable_canonical_fingerprint() -> None:
    """SMA publication semantics are locked by exact canonical bytes and digest."""
    _assert_golden_strategy(
        sma_strategy_payload(),
        "sma_strategy_v1.json",
        "sha256:7a6ae244a523f14decd096103330ce0b072315a6917adbbbb5a29453b28646d8",
    )


def test_volume_sma_strategy_has_stable_canonical_fingerprint() -> None:
    """Volume-SMA publication semantics are locked by exact canonical bytes and digest."""
    _assert_golden_strategy(
        volume_sma_strategy_payload(),
        "volume_sma_strategy_v1.json",
        "sha256:d70ba699a5978b7524496b4bc4d96312a4066dd28bd3f3193b2538c0a0ad8194",
    )


def test_nested_condition_strategy_has_stable_canonical_fingerprint() -> None:
    """Nested condition semantics are locked by exact canonical bytes and digest."""
    _assert_golden_strategy(
        nested_condition_strategy_payload(),
        "nested_condition_strategy_v1.json",
        "sha256:d480c48454b55ce4c412643fe35b63d7da254f4793c20f80feafc8c8659fe55e",
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


def test_strategy_accepts_sma_with_close_input() -> None:
    """The canonical indicator catalog includes close-price SMA definitions."""
    payload = reference_payload()
    indicators = _object_list(payload["indicators"])
    indicators.append(
        {"id": "sma_trend", "kind": "sma", "input": "close", "parameters": {"period": 30}}
    )

    definition = StrategyDefinition.model_validate(payload)

    assert definition.indicators[-1].kind.value == "sma"
    assert definition.indicators[-1].input == "close"


def test_strategy_accepts_volume_sma_with_volume_input() -> None:
    """The canonical indicator catalog includes volume-only SMA definitions."""
    payload = reference_payload()
    indicators = _object_list(payload["indicators"])
    indicators.append(
        {
            "id": "average_volume",
            "kind": "volume_sma",
            "input": "volume",
            "parameters": {"period": 30},
        }
    )

    definition = StrategyDefinition.model_validate(payload)

    assert definition.indicators[-1].kind.value == "volume_sma"
    assert definition.indicators[-1].input == "volume"


def test_sma_indicators_enforce_sources_bounds_and_required_fields() -> None:
    """SMA variants reject wrong sources, out-of-range periods, and undeclared volume."""
    wrong_sma_source = reference_payload()
    _object_list(wrong_sma_source["indicators"]).append(
        {"id": "sma_trend", "kind": "sma", "input": "volume", "parameters": {"period": 30}}
    )
    with pytest.raises(ValidationError, match="sma input must be close"):
        StrategyDefinition.model_validate(wrong_sma_source)

    wrong_volume_source = reference_payload()
    _object_list(wrong_volume_source["indicators"]).append(
        {
            "id": "average_volume",
            "kind": "volume_sma",
            "input": "close",
            "parameters": {"period": 30},
        }
    )
    with pytest.raises(ValidationError, match="volume_sma input must be volume"):
        StrategyDefinition.model_validate(wrong_volume_source)

    excessive_period = reference_payload()
    _object_list(excessive_period["indicators"]).append(
        {"id": "sma_trend", "kind": "sma", "input": "close", "parameters": {"period": 501}}
    )
    with pytest.raises(ValidationError, match="less than or equal to 500"):
        StrategyDefinition.model_validate(excessive_period)

    missing_volume = reference_payload()
    _object_list(missing_volume["indicators"]).append(
        {
            "id": "average_volume",
            "kind": "volume_sma",
            "input": "volume",
            "parameters": {"period": 30},
        }
    )
    missing_volume["data_requirements"] = {
        "warmup_bars": 50,
        "required_fields": ["open", "high", "low", "close"],
    }
    with pytest.raises(ValidationError, match="required_fields"):
        StrategyDefinition.model_validate(missing_volume)


def test_strategy_accepts_nested_any_condition_group() -> None:
    """Entry conditions may nest a non-empty OR group beneath the root AND group."""
    payload = reference_payload()
    entry = _object_mapping(payload["entry"])
    entry["when"] = {
        "all": [
            {
                "left": {"indicator": "ema_fast"},
                "operator": "crosses_above",
                "right": {"indicator": "ema_slow"},
            },
            {
                "any": [
                    {
                        "left": {"indicator": "rsi"},
                        "operator": "greater_than",
                        "right": {"literal": "50"},
                    },
                    {
                        "left": {"indicator": "ema_fast"},
                        "operator": "greater_than",
                        "right": {"indicator": "ema_slow"},
                    },
                ]
            },
        ]
    }

    definition = StrategyDefinition.model_validate(payload)

    assert definition.entry.when.model_dump(mode="json") == entry["when"]


def test_strategy_accepts_unary_not_condition_group() -> None:
    """A NOT group wraps exactly one child and retains its public canonical key."""
    payload = reference_payload()
    entry = _object_mapping(payload["entry"])
    entry["when"] = {
        "any": [
            {
                "not": {
                    "left": {"indicator": "rsi"},
                    "operator": "less_than",
                    "right": {"literal": "50"},
                }
            },
            {
                "left": {"indicator": "ema_fast"},
                "operator": "crosses_above",
                "right": {"indicator": "ema_slow"},
            },
        ]
    }

    definition = StrategyDefinition.model_validate(payload)

    assert b'"not":' in canonical_strategy_bytes(definition)


def test_strategy_limits_condition_tree_depth() -> None:
    """Condition trees accept four levels and reject a fifth level."""
    maximum_depth = reference_payload()
    maximum_entry = _object_mapping(maximum_depth["entry"])
    maximum_entry["when"] = {"all": [{"any": [{"not": _ema_comparison_payload()}]}]}
    StrategyDefinition.model_validate(maximum_depth)

    excessive_depth = reference_payload()
    excessive_entry = _object_mapping(excessive_depth["entry"])
    excessive_entry["when"] = {"all": [{"any": [{"not": {"not": _ema_comparison_payload()}}]}]}
    with pytest.raises(ValidationError, match="condition tree depth"):
        StrategyDefinition.model_validate(excessive_depth)


def test_strategy_limits_condition_tree_node_count() -> None:
    """Condition trees accept 64 total nodes and reject a sixty-fifth node."""
    sixty_comparisons = [
        {"any": [_ema_comparison_payload() for _index in range(20)]} for _group in range(3)
    ]
    maximum_nodes = reference_payload()
    maximum_entry = _object_mapping(maximum_nodes["entry"])
    maximum_entry["when"] = {"all": sixty_comparisons}
    StrategyDefinition.model_validate(maximum_nodes)

    excessive_nodes = reference_payload()
    excessive_entry = _object_mapping(excessive_nodes["entry"])
    excessive_entry["when"] = {"all": [*sixty_comparisons, _ema_comparison_payload()]}
    with pytest.raises(ValidationError, match="condition tree node count"):
        StrategyDefinition.model_validate(excessive_nodes)


def test_nested_conditions_reject_empty_malformed_and_unknown_children() -> None:
    """Recursive groups fail closed on empty, non-unary, and unresolved child shapes."""
    empty_group = reference_payload()
    _object_mapping(empty_group["entry"])["when"] = {"any": []}
    with pytest.raises(ValidationError, match="at least 1 item"):
        StrategyDefinition.model_validate(empty_group)

    malformed_not = reference_payload()
    _object_mapping(malformed_not["entry"])["when"] = {
        "not": [_ema_comparison_payload(), _ema_comparison_payload()]
    }
    with pytest.raises(ValidationError):
        StrategyDefinition.model_validate(malformed_not)

    unknown_nested_reference = nested_condition_strategy_payload()
    nested_entry = _object_mapping(unknown_nested_reference["entry"])
    nested_entry["when"] = {
        "all": [
            {
                "any": [
                    {
                        "not": {
                            "left": {"indicator": "missing"},
                            "operator": "greater_than",
                            "right": {"literal": "1"},
                        }
                    }
                ]
            }
        ]
    }
    with pytest.raises(ValidationError, match="unknown indicator"):
        StrategyDefinition.model_validate(unknown_nested_reference)


def test_condition_fingerprint_preserves_authored_order_and_nesting() -> None:
    """Condition ordering and grouping remain explicit parts of immutable identity."""
    original_payload = nested_condition_strategy_payload()
    original = StrategyDefinition.model_validate(original_payload)

    reordered_payload = nested_condition_strategy_payload()
    reordered_entry = _object_mapping(reordered_payload["entry"])
    reordered_when = _object_mapping(reordered_entry["when"])
    reordered_all = _object_list(reordered_when["all"])
    reordered_any = _object_mapping(reordered_all[1])
    reordered_children = _object_list(reordered_any["any"])
    reordered_any["any"] = list(reversed(reordered_children))
    reordered = StrategyDefinition.model_validate(reordered_payload)

    regrouped_payload = nested_condition_strategy_payload()
    regrouped_entry = _object_mapping(regrouped_payload["entry"])
    regrouped_when = _object_mapping(regrouped_entry["when"])
    regrouped_all = _object_list(regrouped_when["all"])
    regrouped_any = _object_mapping(regrouped_all[1])
    regrouped_all[1] = {"all": regrouped_any["any"]}
    regrouped = StrategyDefinition.model_validate(regrouped_payload)

    assert strategy_fingerprint(reordered) != strategy_fingerprint(original)
    assert strategy_fingerprint(regrouped) != strategy_fingerprint(original)


def test_volume_sma_period_contributes_to_required_warmup() -> None:
    """Volume-SMA periods participate in the strategy-wide warmup requirement."""
    payload = volume_sma_strategy_payload()
    indicators = _object_list(payload["indicators"])
    _object_mapping(indicators[-1])["parameters"] = {"period": 60}

    with pytest.raises(ValidationError, match="warmup"):
        StrategyDefinition.model_validate(payload)

    payload["data_requirements"] = {
        "warmup_bars": 60,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    StrategyDefinition.model_validate(payload)


def test_strategy_identity_helpers_revalidate_copied_models() -> None:
    """Canonical strategy identities reject instances forged by unchecked model copies."""
    strategy = StrategyDefinition.model_validate(reference_payload())
    forged = strategy.model_copy(update={"name": ""})

    with pytest.raises(ValidationError):
        canonical_strategy_bytes(forged)
    with pytest.raises(ValidationError):
        strategy_fingerprint(forged)
