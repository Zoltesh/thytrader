"""Tests for the first canonical strategy-schema publication profile."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import ValidationError
import pytest

from thytrader.strategies.models import (
    ConstantIndicatorParameters,
    StrategyDefinition,
    StrategyStatus,
    canonical_strategy_bytes,
    expanded_data_requirements,
    is_valid_htf_pair,
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


def test_strategy_accepts_five_minute_research_timeframe() -> None:
    """Research strategies may bind 5m datasets; 15m, 30m, 6h, and 1d remain unsupported."""
    payload = reference_payload()
    payload["timeframe"] = "5m"
    definition = StrategyDefinition.model_validate(payload)
    assert definition.timeframe == "5m"
    payload["timeframe"] = "15m"
    with pytest.raises(ValidationError):
        StrategyDefinition.model_validate(payload)
    payload["timeframe"] = "30m"
    with pytest.raises(ValidationError):
        StrategyDefinition.model_validate(payload)
    payload["timeframe"] = "6h"
    with pytest.raises(ValidationError):
        StrategyDefinition.model_validate(payload)
    payload["timeframe"] = "1d"
    with pytest.raises(ValidationError):
        StrategyDefinition.model_validate(payload)


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


def test_strategy_accepts_highest_lowest_and_stdev_with_locked_inputs() -> None:
    """Phase 9 single-output kinds publish with their registry-locked sources."""
    payload = reference_payload()
    indicators = _object_list(payload["indicators"])
    indicators.extend(
        (
            {
                "id": "channel_high",
                "kind": "highest",
                "input": "high",
                "parameters": {"period": 20},
            },
            {"id": "channel_low", "kind": "lowest", "input": "low", "parameters": {"period": 20}},
            {"id": "close_stdev", "kind": "stdev", "input": "close", "parameters": {"period": 20}},
        )
    )
    entry = _object_mapping(payload["entry"])
    entry["when"] = {
        "all": [
            {
                "left": {"indicator": "ema_fast"},
                "operator": "crosses_above",
                "right": {"indicator": "channel_high"},
            },
            {
                "left": {"indicator": "close_stdev"},
                "operator": "greater_than",
                "right": {"literal": "10"},
            },
            {
                "left": {"indicator": "ema_slow"},
                "operator": "greater_than",
                "right": {"indicator": "channel_low"},
            },
        ]
    }

    definition = StrategyDefinition.model_validate(payload)

    by_id = {indicator.id: indicator for indicator in definition.indicators}
    assert by_id["channel_high"].kind.value == "highest"
    assert by_id["channel_high"].input == "high"
    assert by_id["channel_low"].kind.value == "lowest"
    assert by_id["channel_low"].input == "low"
    assert by_id["close_stdev"].kind.value == "stdev"
    assert by_id["close_stdev"].input == "close"


def test_highest_lowest_stdev_reject_wrong_sources_unknown_kinds_and_fields() -> None:
    """Unknown kinds, extra fields, and unlocked inputs fail closed."""
    wrong_highest = reference_payload()
    _object_list(wrong_highest["indicators"]).append(
        {"id": "channel_high", "kind": "highest", "input": "close", "parameters": {"period": 20}}
    )
    with pytest.raises(ValidationError, match="highest input must be high"):
        StrategyDefinition.model_validate(wrong_highest)

    wrong_lowest = reference_payload()
    _object_list(wrong_lowest["indicators"]).append(
        {"id": "channel_low", "kind": "lowest", "input": "high", "parameters": {"period": 20}}
    )
    with pytest.raises(ValidationError, match="lowest input must be low"):
        StrategyDefinition.model_validate(wrong_lowest)

    wrong_stdev = reference_payload()
    _object_list(wrong_stdev["indicators"]).append(
        {"id": "close_stdev", "kind": "stdev", "input": "volume", "parameters": {"period": 20}}
    )
    with pytest.raises(ValidationError, match="stdev input must be close"):
        StrategyDefinition.model_validate(wrong_stdev)

    unknown_kind = reference_payload()
    _object_list(unknown_kind["indicators"]).append(
        {"id": "stoch", "kind": "stochastic", "input": "close", "parameters": {"period": 14}}
    )
    with pytest.raises(ValidationError, match="stochastic"):
        StrategyDefinition.model_validate(unknown_kind)

    extra_parameter = reference_payload()
    _object_list(extra_parameter["indicators"]).append(
        {
            "id": "close_stdev",
            "kind": "stdev",
            "input": "close",
            "parameters": {"period": 20, "ddof": 1},
        }
    )
    with pytest.raises(ValidationError, match="Extra inputs"):
        StrategyDefinition.model_validate(extra_parameter)

    missing_high = reference_payload()
    _object_list(missing_high["indicators"]).append(
        {"id": "channel_high", "kind": "highest", "input": "high", "parameters": {"period": 20}}
    )
    missing_high["data_requirements"] = {
        "warmup_bars": 50,
        "required_fields": ["open", "low", "close", "volume"],
    }
    with pytest.raises(ValidationError, match="required_fields"):
        StrategyDefinition.model_validate(missing_high)


def test_htf_filter_accepts_highest_lowest_and_stdev() -> None:
    """HTF filter indicators use the same locked catalog as LTF, still research-only."""
    payload = reference_payload()
    payload["htf_filter"] = _htf_filter_block()
    htf = _object_mapping(payload["htf_filter"])
    htf["indicators"] = [
        {"id": "htf_high", "kind": "highest", "input": "high", "parameters": {"period": 20}},
        {"id": "htf_low", "kind": "lowest", "input": "low", "parameters": {"period": 20}},
        {"id": "htf_stdev", "kind": "stdev", "input": "close", "parameters": {"period": 20}},
    ]
    htf["when"] = {
        "all": [
            {
                "left": {"indicator": "htf_stdev"},
                "operator": "greater_than",
                "right": {"literal": "1"},
            }
        ]
    }

    definition = StrategyDefinition.model_validate(payload)

    assert definition.htf_filter is not None
    assert [indicator.kind.value for indicator in definition.htf_filter.indicators] == [
        "highest",
        "lowest",
        "stdev",
    ]


def test_strategy_accepts_roc_williams_r_and_cci_with_locked_inputs() -> None:
    """Phase 9 slice 2 kinds publish with registry-locked sources and period bounds."""
    payload = reference_payload()
    indicators = _object_list(payload["indicators"])
    indicators.extend(
        (
            {"id": "close_roc", "kind": "roc", "input": "close", "parameters": {"period": 20}},
            {
                "id": "willr_14",
                "kind": "williams_r",
                "input": ["high", "low", "close"],
                "parameters": {"period": 14},
            },
            {
                "id": "cci_14",
                "kind": "cci",
                "input": ["high", "low", "close"],
                "parameters": {"period": 14},
            },
        )
    )
    entry = _object_mapping(payload["entry"])
    entry["when"] = {
        "all": [
            {
                "left": {"indicator": "close_roc"},
                "operator": "greater_than",
                "right": {"literal": "0"},
            },
            {
                "left": {"indicator": "willr_14"},
                "operator": "less_than",
                "right": {"literal": "-20"},
            },
            {
                "left": {"indicator": "cci_14"},
                "operator": "greater_than",
                "right": {"literal": "100"},
            },
        ]
    }

    definition = StrategyDefinition.model_validate(payload)

    by_id = {indicator.id: indicator for indicator in definition.indicators}
    assert by_id["close_roc"].kind.value == "roc"
    assert by_id["close_roc"].input == "close"
    assert by_id["willr_14"].kind.value == "williams_r"
    assert by_id["willr_14"].input == ("high", "low", "close")
    assert by_id["cci_14"].kind.value == "cci"
    assert by_id["cci_14"].input == ("high", "low", "close")


def test_roc_williams_r_and_cci_reject_wrong_sources_and_period_bounds() -> None:
    """Unlocked inputs and oscillator periods above 100 fail closed."""
    wrong_roc = reference_payload()
    _object_list(wrong_roc["indicators"]).append(
        {"id": "close_roc", "kind": "roc", "input": "high", "parameters": {"period": 20}}
    )
    with pytest.raises(ValidationError, match="roc input must be close"):
        StrategyDefinition.model_validate(wrong_roc)

    wrong_willr = reference_payload()
    _object_list(wrong_willr["indicators"]).append(
        {"id": "willr_14", "kind": "williams_r", "input": "close", "parameters": {"period": 14}}
    )
    with pytest.raises(
        ValidationError, match="williams_r input must be high, low, close in canonical order"
    ):
        StrategyDefinition.model_validate(wrong_willr)

    wrong_cci = reference_payload()
    _object_list(wrong_cci["indicators"]).append(
        {"id": "cci_14", "kind": "cci", "input": "close", "parameters": {"period": 14}}
    )
    with pytest.raises(
        ValidationError, match="cci input must be high, low, close in canonical order"
    ):
        StrategyDefinition.model_validate(wrong_cci)

    long_willr = reference_payload()
    _object_list(long_willr["indicators"]).append(
        {
            "id": "willr_14",
            "kind": "williams_r",
            "input": ["high", "low", "close"],
            "parameters": {"period": 101},
        }
    )
    with pytest.raises(ValidationError, match="WILLIAMS_R period exceeds 100"):
        StrategyDefinition.model_validate(long_willr)

    long_cci = reference_payload()
    _object_list(long_cci["indicators"]).append(
        {
            "id": "cci_14",
            "kind": "cci",
            "input": ["high", "low", "close"],
            "parameters": {"period": 101},
        }
    )
    with pytest.raises(ValidationError, match="CCI period exceeds 100"):
        StrategyDefinition.model_validate(long_cci)


def test_roc_lookback_contributes_an_extra_warmup_bar() -> None:
    """ROC needs period + 1 closed bars before the first defined value."""
    payload = reference_payload()
    _object_list(payload["indicators"]).append(
        {"id": "close_roc", "kind": "roc", "input": "close", "parameters": {"period": 50}}
    )
    payload["data_requirements"] = {
        "warmup_bars": 50,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    with pytest.raises(ValidationError, match="warmup"):
        StrategyDefinition.model_validate(payload)

    payload["data_requirements"] = {
        "warmup_bars": 51,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    StrategyDefinition.model_validate(payload)


def test_htf_filter_accepts_roc_williams_r_and_cci() -> None:
    """HTF filter may declare the Phase 9 slice 2 kinds on the HTF clock."""
    payload = reference_payload()
    payload["htf_filter"] = _htf_filter_block()
    htf = _object_mapping(payload["htf_filter"])
    htf["indicators"] = [
        {"id": "htf_roc", "kind": "roc", "input": "close", "parameters": {"period": 20}},
        {
            "id": "htf_willr",
            "kind": "williams_r",
            "input": ["high", "low", "close"],
            "parameters": {"period": 14},
        },
        {
            "id": "htf_cci",
            "kind": "cci",
            "input": ["high", "low", "close"],
            "parameters": {"period": 14},
        },
    ]
    htf["when"] = {
        "all": [
            {
                "left": {"indicator": "htf_roc"},
                "operator": "greater_than",
                "right": {"literal": "0"},
            }
        ]
    }

    definition = StrategyDefinition.model_validate(payload)

    assert definition.htf_filter is not None
    assert [indicator.kind.value for indicator in definition.htf_filter.indicators] == [
        "roc",
        "williams_r",
        "cci",
    ]


def test_strategy_accepts_identity_and_constant_with_crossover_levels() -> None:
    """Identity close and a named level publish and may be crossover operands."""
    payload = reference_payload()
    indicators = _object_list(payload["indicators"])
    indicators.extend(
        (
            {"id": "px", "kind": "identity", "input": "close", "parameters": {}},
            {"id": "vol", "kind": "identity", "input": "volume", "parameters": {}},
            {"id": "session_open", "kind": "identity", "input": "open", "parameters": {}},
            {"id": "rsi_level", "kind": "constant", "parameters": {"value": "40"}},
        )
    )
    entry = _object_mapping(payload["entry"])
    entry["when"] = {
        "all": [
            {
                "left": {"indicator": "px"},
                "operator": "crosses_above",
                "right": {"indicator": "ema_slow"},
            },
            {
                "left": {"indicator": "rsi"},
                "operator": "crosses_above",
                "right": {"indicator": "rsi_level"},
            },
            {
                "left": {"indicator": "vol"},
                "operator": "greater_than",
                "right": {"literal": "0"},
            },
        ]
    }

    definition = StrategyDefinition.model_validate(payload)

    by_id = {indicator.id: indicator for indicator in definition.indicators}
    assert by_id["px"].kind.value == "identity"
    assert by_id["px"].input == "close"
    assert by_id["px"].parameters.model_dump() == {}
    assert by_id["vol"].input == "volume"
    assert by_id["session_open"].input == "open"
    assert by_id["rsi_level"].kind.value == "constant"
    assert by_id["rsi_level"].input is None
    parameters = by_id["rsi_level"].parameters
    assert isinstance(parameters, ConstantIndicatorParameters)
    assert parameters.value == "40"
    canonical = canonical_strategy_bytes(definition).decode()
    assert '"id":"rsi_level","kind":"constant","parameters":{"value":"40"}' in canonical
    assert '"input":null' not in canonical


def test_identity_and_constant_reject_wrong_shape() -> None:
    """Period leftovers, unlocked sources, and constant inputs fail closed."""
    period_on_identity = reference_payload()
    _object_list(period_on_identity["indicators"]).append(
        {"id": "px", "kind": "identity", "input": "close", "parameters": {"period": 20}}
    )
    with pytest.raises(ValidationError, match="identity parameters must be an empty object"):
        StrategyDefinition.model_validate(period_on_identity)

    bad_identity_input = reference_payload()
    _object_list(bad_identity_input["indicators"]).append(
        {
            "id": "px",
            "kind": "identity",
            "input": ["high", "low", "close"],
            "parameters": {},
        }
    )
    with pytest.raises(
        ValidationError, match="identity input must be one of open, high, low, close, volume"
    ):
        StrategyDefinition.model_validate(bad_identity_input)

    constant_with_input = reference_payload()
    _object_list(constant_with_input["indicators"]).append(
        {
            "id": "rsi_level",
            "kind": "constant",
            "input": "close",
            "parameters": {"value": "40"},
        }
    )
    with pytest.raises(ValidationError, match="constant must omit input"):
        StrategyDefinition.model_validate(constant_with_input)

    period_on_constant = reference_payload()
    _object_list(period_on_constant["indicators"]).append(
        {"id": "rsi_level", "kind": "constant", "parameters": {"period": 14}}
    )
    with pytest.raises(ValidationError, match="constant parameters must declare value"):
        StrategyDefinition.model_validate(period_on_constant)

    empty_on_ema = reference_payload()
    _object_list(empty_on_ema["indicators"]).append(
        {"id": "ema_extra", "kind": "ema", "input": "close", "parameters": {}}
    )
    with pytest.raises(ValidationError, match="ema parameters must declare period"):
        StrategyDefinition.model_validate(empty_on_ema)

    crossover_literal = reference_payload()
    entry = _object_mapping(crossover_literal["entry"])
    entry["when"] = {
        "all": [
            {
                "left": {"indicator": "rsi"},
                "operator": "crosses_above",
                "right": {"literal": "40"},
            }
        ]
    }
    with pytest.raises(
        ValidationError, match="crossover right operand must reference an indicator"
    ):
        StrategyDefinition.model_validate(crossover_literal)


def test_identity_and_constant_require_one_warmup_bar() -> None:
    """Identity and constant are defined on the first completed bar."""
    payload = reference_payload()
    payload["indicators"] = [
        {"id": "px", "kind": "identity", "input": "close", "parameters": {}},
        {"id": "rsi_level", "kind": "constant", "parameters": {"value": "40"}},
        {
            "id": "atr",
            "kind": "atr",
            "input": ["high", "low", "close"],
            "parameters": {"period": 2},
        },
    ]
    entry = _object_mapping(payload["entry"])
    entry["when"] = {
        "all": [
            {
                "left": {"indicator": "px"},
                "operator": "greater_than",
                "right": {"indicator": "rsi_level"},
            }
        ]
    }
    payload["data_requirements"] = {
        "warmup_bars": 1,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    with pytest.raises(ValidationError, match="warmup"):
        StrategyDefinition.model_validate(payload)

    payload["data_requirements"]["warmup_bars"] = 2
    definition = StrategyDefinition.model_validate(payload)
    assert [indicator.kind.value for indicator in definition.indicators] == [
        "identity",
        "constant",
        "atr",
    ]


def test_htf_filter_accepts_identity_and_constant() -> None:
    """HTF filter may declare identity OHLCV and named levels on the HTF clock."""
    payload = reference_payload()
    payload["htf_filter"] = _htf_filter_block()
    htf = _object_mapping(payload["htf_filter"])
    htf["indicators"] = [
        {"id": "htf_px", "kind": "identity", "input": "close", "parameters": {}},
        {"id": "htf_level", "kind": "constant", "parameters": {"value": "0"}},
    ]
    htf["when"] = {
        "all": [
            {
                "left": {"indicator": "htf_px"},
                "operator": "greater_than",
                "right": {"indicator": "htf_level"},
            }
        ]
    }

    definition = StrategyDefinition.model_validate(payload)

    assert definition.htf_filter is not None
    assert [indicator.kind.value for indicator in definition.htf_filter.indicators] == [
        "identity",
        "constant",
    ]


def test_strategy_accepts_wma_momentum_and_mfi_with_locked_inputs() -> None:
    """Phase 9 slice 4 kinds publish with registry-locked sources and period bounds."""
    payload = reference_payload()
    indicators = _object_list(payload["indicators"])
    indicators.extend(
        (
            {"id": "close_wma", "kind": "wma", "input": "close", "parameters": {"period": 20}},
            {
                "id": "close_mom",
                "kind": "momentum",
                "input": "close",
                "parameters": {"period": 20},
            },
            {
                "id": "mfi_14",
                "kind": "mfi",
                "input": ["high", "low", "close", "volume"],
                "parameters": {"period": 14},
            },
        )
    )
    payload["data_requirements"] = {
        "warmup_bars": 250,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    entry = _object_mapping(payload["entry"])
    entry["when"] = {
        "all": [
            {
                "left": {"indicator": "close_wma"},
                "operator": "greater_than",
                "right": {"indicator": "close_mom"},
            },
            {
                "left": {"indicator": "mfi_14"},
                "operator": "greater_than",
                "right": {"literal": "50"},
            },
        ]
    }

    definition = StrategyDefinition.model_validate(payload)

    by_id = {indicator.id: indicator for indicator in definition.indicators}
    assert by_id["close_wma"].kind.value == "wma"
    assert by_id["close_wma"].input == "close"
    assert by_id["close_mom"].kind.value == "momentum"
    assert by_id["close_mom"].input == "close"
    assert by_id["mfi_14"].kind.value == "mfi"
    assert by_id["mfi_14"].input == ("high", "low", "close", "volume")


def test_wma_momentum_and_mfi_reject_wrong_sources_and_period_bounds() -> None:
    """Unlocked inputs and MFI periods above 100 fail closed."""
    wrong_wma = reference_payload()
    _object_list(wrong_wma["indicators"]).append(
        {"id": "close_wma", "kind": "wma", "input": "high", "parameters": {"period": 20}}
    )
    with pytest.raises(ValidationError, match="wma input must be close"):
        StrategyDefinition.model_validate(wrong_wma)

    wrong_mom = reference_payload()
    _object_list(wrong_mom["indicators"]).append(
        {"id": "close_mom", "kind": "momentum", "input": "volume", "parameters": {"period": 20}}
    )
    with pytest.raises(ValidationError, match="momentum input must be close"):
        StrategyDefinition.model_validate(wrong_mom)

    wrong_mfi = reference_payload()
    _object_list(wrong_mfi["indicators"]).append(
        {
            "id": "mfi_14",
            "kind": "mfi",
            "input": ["high", "low", "close"],
            "parameters": {"period": 14},
        }
    )
    with pytest.raises(
        ValidationError, match="mfi input must be high, low, close, volume in canonical order"
    ):
        StrategyDefinition.model_validate(wrong_mfi)

    long_mfi = reference_payload()
    _object_list(long_mfi["indicators"]).append(
        {
            "id": "mfi_14",
            "kind": "mfi",
            "input": ["high", "low", "close", "volume"],
            "parameters": {"period": 101},
        }
    )
    with pytest.raises(ValidationError, match="MFI period exceeds 100"):
        StrategyDefinition.model_validate(long_mfi)


def test_momentum_and_mfi_lookback_contribute_an_extra_warmup_bar() -> None:
    """Momentum and MFI need period + 1 closed bars before the first defined value."""
    payload = reference_payload()
    _object_list(payload["indicators"]).extend(
        (
            {"id": "close_mom", "kind": "momentum", "input": "close", "parameters": {"period": 50}},
            {
                "id": "mfi_14",
                "kind": "mfi",
                "input": ["high", "low", "close", "volume"],
                "parameters": {"period": 50},
            },
        )
    )
    payload["data_requirements"] = {
        "warmup_bars": 50,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    with pytest.raises(ValidationError, match="warmup"):
        StrategyDefinition.model_validate(payload)

    payload["data_requirements"] = {
        "warmup_bars": 51,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    StrategyDefinition.model_validate(payload)


def test_htf_filter_accepts_wma_momentum_and_mfi() -> None:
    """HTF filter may declare the Phase 9 slice 4 kinds on the HTF clock."""
    payload = reference_payload()
    payload["htf_filter"] = _htf_filter_block()
    htf = _object_mapping(payload["htf_filter"])
    htf["indicators"] = [
        {"id": "htf_wma", "kind": "wma", "input": "close", "parameters": {"period": 20}},
        {"id": "htf_mom", "kind": "momentum", "input": "close", "parameters": {"period": 20}},
        {
            "id": "htf_mfi",
            "kind": "mfi",
            "input": ["high", "low", "close", "volume"],
            "parameters": {"period": 14},
        },
    ]
    htf["when"] = {
        "all": [
            {
                "left": {"indicator": "htf_wma"},
                "operator": "greater_than",
                "right": {"literal": "0"},
            }
        ]
    }

    definition = StrategyDefinition.model_validate(payload)

    assert definition.htf_filter is not None
    assert [indicator.kind.value for indicator in definition.htf_filter.indicators] == [
        "wma",
        "momentum",
        "mfi",
    ]


def test_strategy_accepts_macd_and_bollinger_with_series_operands() -> None:
    """Phase 9 slice 5 kinds publish with locked close and required series ids."""
    payload = reference_payload()
    indicators = _object_list(payload["indicators"])
    indicators.extend(
        (
            {
                "id": "trend_macd",
                "kind": "macd",
                "input": "close",
                "parameters": {"fast_period": 12, "slow_period": 26, "signal_period": 9},
            },
            {
                "id": "bands",
                "kind": "bollinger",
                "input": "close",
                "parameters": {"period": 20, "stdev_multiplier": "2"},
            },
        )
    )
    payload["data_requirements"] = {
        "warmup_bars": 250,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    entry = _object_mapping(payload["entry"])
    entry["when"] = {
        "all": [
            {
                "left": {"indicator": "trend_macd", "series": "macd"},
                "operator": "crosses_above",
                "right": {"indicator": "trend_macd", "series": "signal"},
            },
            {
                "left": {"indicator": "bands", "series": "lower"},
                "operator": "less_than",
                "right": {"literal": "100"},
            },
        ]
    }

    definition = StrategyDefinition.model_validate(payload)

    by_id = {indicator.id: indicator for indicator in definition.indicators}
    assert by_id["trend_macd"].kind.value == "macd"
    assert by_id["trend_macd"].input == "close"
    assert by_id["bands"].kind.value == "bollinger"
    assert by_id["bands"].input == "close"
    canonical = canonical_strategy_bytes(definition).decode()
    assert '"series":"macd"' in canonical
    assert '"series":null' not in canonical


def test_macd_and_bollinger_reject_wrong_params_sources_and_series() -> None:
    """Unlocked inputs, missing series, and invalid periods fail closed."""
    missing_series = reference_payload()
    _object_list(missing_series["indicators"]).append(
        {
            "id": "trend_macd",
            "kind": "macd",
            "input": "close",
            "parameters": {"fast_period": 12, "slow_period": 26, "signal_period": 9},
        }
    )
    entry = _object_mapping(missing_series["entry"])
    entry["when"] = {
        "all": [
            {
                "left": {"indicator": "trend_macd"},
                "operator": "greater_than",
                "right": {"literal": "0"},
            }
        ]
    }
    with pytest.raises(ValidationError, match="macd series must be one of"):
        StrategyDefinition.model_validate(missing_series)

    series_on_sma = reference_payload()
    sma_entry = _object_mapping(series_on_sma["entry"])
    sma_entry["when"] = {
        "all": [
            {
                "left": {"indicator": "ema_fast", "series": "macd"},
                "operator": "greater_than",
                "right": {"literal": "0"},
            }
        ]
    }
    with pytest.raises(ValidationError, match="ema operand must omit series"):
        StrategyDefinition.model_validate(series_on_sma)

    wrong_macd_input = reference_payload()
    _object_list(wrong_macd_input["indicators"]).append(
        {
            "id": "trend_macd",
            "kind": "macd",
            "input": "high",
            "parameters": {"fast_period": 12, "slow_period": 26, "signal_period": 9},
        }
    )
    with pytest.raises(ValidationError, match="macd input must be close"):
        StrategyDefinition.model_validate(wrong_macd_input)

    inverted = reference_payload()
    _object_list(inverted["indicators"]).append(
        {
            "id": "trend_macd",
            "kind": "macd",
            "input": "close",
            "parameters": {"fast_period": 26, "slow_period": 12, "signal_period": 9},
        }
    )
    with pytest.raises(ValidationError, match="fast_period must be less than slow_period"):
        StrategyDefinition.model_validate(inverted)

    zero_width = reference_payload()
    _object_list(zero_width["indicators"]).append(
        {
            "id": "bands",
            "kind": "bollinger",
            "input": "close",
            "parameters": {"period": 20, "stdev_multiplier": "0"},
        }
    )
    with pytest.raises(ValidationError, match="stdev_multiplier must be greater than 0"):
        StrategyDefinition.model_validate(zero_width)

    period_only_macd = reference_payload()
    _object_list(period_only_macd["indicators"]).append(
        {"id": "trend_macd", "kind": "macd", "input": "close", "parameters": {"period": 12}}
    )
    with pytest.raises(ValidationError, match="macd parameters must declare"):
        StrategyDefinition.model_validate(period_only_macd)


def test_macd_warmup_covers_slow_plus_signal_minus_one() -> None:
    """MACD signal needs slow_period + signal_period - 1 closed bars."""
    payload = reference_payload()
    _object_list(payload["indicators"]).append(
        {
            "id": "trend_macd",
            "kind": "macd",
            "input": "close",
            "parameters": {"fast_period": 12, "slow_period": 50, "signal_period": 2},
        }
    )
    payload["data_requirements"] = {
        "warmup_bars": 50,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    with pytest.raises(ValidationError, match="warmup"):
        StrategyDefinition.model_validate(payload)

    payload["data_requirements"]["warmup_bars"] = 51
    StrategyDefinition.model_validate(payload)


def test_htf_filter_accepts_macd_and_bollinger() -> None:
    """HTF filter may declare MACD and Bollinger on the HTF clock."""
    payload = reference_payload()
    payload["htf_filter"] = _htf_filter_block()
    htf = _object_mapping(payload["htf_filter"])
    htf["indicators"] = [
        {
            "id": "htf_macd",
            "kind": "macd",
            "input": "close",
            "parameters": {"fast_period": 12, "slow_period": 26, "signal_period": 9},
        },
        {
            "id": "htf_bands",
            "kind": "bollinger",
            "input": "close",
            "parameters": {"period": 20, "stdev_multiplier": "2"},
        },
    ]
    htf["when"] = {
        "all": [
            {
                "left": {"indicator": "htf_macd", "series": "histogram"},
                "operator": "greater_than",
                "right": {"literal": "0"},
            }
        ]
    }

    definition = StrategyDefinition.model_validate(payload)

    assert definition.htf_filter is not None
    assert [indicator.kind.value for indicator in definition.htf_filter.indicators] == [
        "macd",
        "bollinger",
    ]


def test_strategy_identity_helpers_revalidate_copied_models() -> None:
    """Canonical strategy identities reject instances forged by unchecked model copies."""
    strategy = StrategyDefinition.model_validate(reference_payload())
    forged = strategy.model_copy(update={"name": ""})

    with pytest.raises(ValidationError):
        canonical_strategy_bytes(forged)
    with pytest.raises(ValidationError):
        strategy_fingerprint(forged)


def _htf_filter_block(
    *,
    timeframe: str = "6h",
    warmup_bars: int = 50,
    fast_id: str = "htf_ema_fast",
) -> dict[str, object]:
    """Return one valid HTF filter for the 1h reference profile."""
    return {
        "timeframe": timeframe,
        "data_requirements": {
            "warmup_bars": warmup_bars,
            "required_fields": ["open", "high", "low", "close", "volume"],
        },
        "indicators": [
            {
                "id": fast_id,
                "kind": "ema",
                "input": "close",
                "parameters": {"period": 20},
            },
            {
                "id": "htf_ema_slow",
                "kind": "ema",
                "input": "close",
                "parameters": {"period": 50},
            },
        ],
        "when": {
            "all": [
                {
                    "left": {"indicator": fast_id},
                    "operator": "greater_than",
                    "right": {"indicator": "htf_ema_slow"},
                }
            ]
        },
    }


def test_omitted_htf_filter_preserves_reference_fingerprint() -> None:
    """Compatible schema extension must not change existing single-timeframe identity."""
    definition = StrategyDefinition.model_validate(reference_payload())
    assert definition.htf_filter is None
    assert strategy_fingerprint(definition) == (
        "sha256:9109f4a024c595ee769a5886a0f147208e2a01c86c26e34aec08dfccdf0f4ea3"
    )


def test_htf_filter_is_fail_closed_and_fingerprinted() -> None:
    """HTF clocks must be coarser integer multiples with isolated indicator identity."""
    assert is_valid_htf_pair("5m", "1h")
    assert is_valid_htf_pair("1h", "6h")
    assert not is_valid_htf_pair("1h", "1h")
    assert not is_valid_htf_pair("1h", "15m")
    assert not is_valid_htf_pair("5m", "5m")

    valid = reference_payload()
    valid["htf_filter"] = _htf_filter_block()
    definition = StrategyDefinition.model_validate(valid)
    assert definition.htf_filter is not None
    assert definition.htf_filter.timeframe == "6h"
    requirements = expanded_data_requirements(definition)
    assert [item.role for item in requirements] == ["decision", "filter"]
    assert [item.timeframe for item in requirements] == ["1h", "6h"]
    assert strategy_fingerprint(definition) != (
        "sha256:9109f4a024c595ee769a5886a0f147208e2a01c86c26e34aec08dfccdf0f4ea3"
    )

    same_clock = reference_payload()
    same_clock["htf_filter"] = _htf_filter_block(timeframe="1h")
    with pytest.raises(ValidationError, match="strictly coarser"):
        StrategyDefinition.model_validate(same_clock)

    finer = reference_payload()
    finer["htf_filter"] = _htf_filter_block(timeframe="15m")
    with pytest.raises(ValidationError, match="strictly coarser"):
        StrategyDefinition.model_validate(finer)

    five_minute = reference_payload()
    five_minute["timeframe"] = "5m"
    five_minute["htf_filter"] = _htf_filter_block(timeframe="1h")
    StrategyDefinition.model_validate(five_minute)

    reused_id = reference_payload()
    reused_id["htf_filter"] = _htf_filter_block(fast_id="ema_fast")
    with pytest.raises(ValidationError, match="must not reuse"):
        StrategyDefinition.model_validate(reused_id)

    mixed_ref = reference_payload()
    mixed_ref["htf_filter"] = _htf_filter_block()
    htf = _object_mapping(mixed_ref["htf_filter"])
    htf["when"] = {
        "all": [
            {
                "left": {"indicator": "ema_fast"},
                "operator": "greater_than",
                "right": {"indicator": "htf_ema_slow"},
            }
        ]
    }
    with pytest.raises(ValidationError, match="unknown HTF indicator"):
        StrategyDefinition.model_validate(mixed_ref)

    ltf_uses_htf = reference_payload()
    ltf_uses_htf["htf_filter"] = _htf_filter_block()
    ltf_uses_htf["entry"] = {
        "side": "long",
        "when": {
            "all": [
                {
                    "left": {"indicator": "htf_ema_fast"},
                    "operator": "greater_than",
                    "right": {"indicator": "ema_slow"},
                }
            ]
        },
        "cooldown_bars": 3,
        "max_open_positions": 1,
    }
    with pytest.raises(ValidationError, match="unknown indicator"):
        StrategyDefinition.model_validate(ltf_uses_htf)

    short_warmup = reference_payload()
    short_warmup["htf_filter"] = _htf_filter_block(warmup_bars=20)
    with pytest.raises(ValidationError, match="HTF warmup_bars"):
        StrategyDefinition.model_validate(short_warmup)

    unknown_htf_field = reference_payload()
    unknown_htf_field["htf_filter"] = {**_htf_filter_block(), "python": "buy()"}
    with pytest.raises(ValidationError):
        StrategyDefinition.model_validate(unknown_htf_field)


def test_disabled_trailing_rejects_extra_fields() -> None:
    """Disabled trailing must stay ``{"enabled": false}`` so fingerprints remain stable."""
    payload = reference_payload()
    exits = _object_mapping(payload["exits"])
    exits["trailing_stop"] = {"enabled": False, "kind": "atr_multiple"}
    with pytest.raises(ValidationError):
        StrategyDefinition.model_validate(payload)


def test_enabled_atr_trailing_requires_named_ltf_atr() -> None:
    """Enabled ATR trailing is fingerprinted and must name an LTF ATR."""
    payload = reference_payload()
    exits = _object_mapping(payload["exits"])
    exits["trailing_stop"] = {
        "enabled": True,
        "kind": "atr_multiple",
        "atr_indicator": "atr",
        "multiple": "1.5",
    }
    enabled = StrategyDefinition.model_validate(payload)
    assert enabled.exits.trailing_stop.enabled is True
    assert strategy_fingerprint(enabled) != strategy_fingerprint(
        StrategyDefinition.model_validate(reference_payload())
    )
    exits["trailing_stop"] = {
        "enabled": True,
        "kind": "atr_multiple",
        "atr_indicator": "ema_fast",
        "multiple": "1.5",
    }
    with pytest.raises(ValidationError, match="trailing stop indicator must reference an ATR"):
        StrategyDefinition.model_validate(payload)
    exits["trailing_stop"] = {
        "enabled": True,
        "kind": "atr_multiple",
        "atr_indicator": "missing_atr",
        "multiple": "1.5",
    }
    with pytest.raises(ValidationError, match="unknown indicator"):
        StrategyDefinition.model_validate(payload)

