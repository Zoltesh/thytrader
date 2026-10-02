"""Signal-based exits in the strategy schema: validation, identity, summary (ADR 0093)."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from pydantic import ValidationError
import pytest

from thytrader.strategies.models import (
    AllCondition,
    AtrTrailingStop,
    ComparisonCondition,
    ComparisonOperator,
    IndicatorOperand,
    Instrument,
    NoTakeProfit,
    StrategyDefinition,
    canonical_strategy_bytes,
    signal_exit_condition,
    strategy_fingerprint,
)
from thytrader.strategies.summary import strategy_summary
from thytrader.strategies.templates import (
    StrategyTemplateId,
    build_template_definition,
    parse_template_id,
    template_blueprint,
    template_catalog,
)

_GOLDEN = Path(__file__).parent / "golden"
_GOLDEN_FINGERPRINTS = {
    "reference_strategy_v1.json": (
        "sha256:fc71217907b862f63fbe6f2bdfb218071b155d631c6a9c4faf06f86d2a5db954"
    ),
}
_CROSS_BELOW = {
    "all": [
        {
            "left": {"indicator": "ema_fast"},
            "operator": "crosses_below",
            "right": {"indicator": "ema_slow"},
        }
    ]
}


def _reference_payload() -> dict[str, object]:
    """Return the golden reference strategy as a mutable mapping."""
    return cast(
        "dict[str, object]",
        json.loads((_GOLDEN / "reference_strategy_v1.json").read_text(encoding="utf-8")),
    )


def _with_signal_exit(signal_exit: object) -> dict[str, object]:
    """Golden reference payload with one ``exits.signal_exit`` value."""
    payload = _reference_payload()
    exits = cast("dict[str, object]", payload["exits"])
    exits["signal_exit"] = signal_exit
    return payload


@pytest.mark.parametrize("filename", sorted(path.name for path in _GOLDEN.glob("*.json")))
def test_documents_without_signal_exit_keep_their_canonical_bytes(filename: str) -> None:
    """Every golden document round-trips byte-for-byte and never grows a signal_exit key."""
    raw = (_GOLDEN / filename).read_bytes()
    definition = StrategyDefinition.model_validate_json(raw)
    assert definition.exits.signal_exit is None
    assert signal_exit_condition(definition.exits) is None
    assert canonical_strategy_bytes(definition) == raw
    assert b"signal_exit" not in canonical_strategy_bytes(definition)
    expected = _GOLDEN_FINGERPRINTS.get(filename)
    if expected is not None:
        assert strategy_fingerprint(definition) == expected


def test_explicit_null_signal_exit_is_the_same_document() -> None:
    """``signal_exit: null`` normalizes to absent, so the fingerprint does not move."""
    definition = StrategyDefinition.model_validate(_with_signal_exit(None))
    assert strategy_fingerprint(definition) == _GOLDEN_FINGERPRINTS["reference_strategy_v1.json"]


def test_signal_exit_is_canonical_and_changes_the_fingerprint() -> None:
    """A declared exit rule is identity: canonical JSON carries it with sorted keys."""
    definition = StrategyDefinition.model_validate(_with_signal_exit({"when": _CROSS_BELOW}))
    condition = signal_exit_condition(definition.exits)
    assert isinstance(condition, AllCondition)
    canonical = canonical_strategy_bytes(definition)
    assert json.loads(canonical)["exits"]["signal_exit"] == {"when": _CROSS_BELOW}
    assert b'"series"' not in canonical
    fingerprint = strategy_fingerprint(definition)
    assert fingerprint != _GOLDEN_FINGERPRINTS["reference_strategy_v1.json"]
    # Pins the canonical form of the new field so a later change cannot move it silently.
    assert fingerprint == (
        "sha256:5009a4c21cf668a1777c40336f4a0565a038a748dee5777c0ad6807295242784"
    )
    assert StrategyDefinition.model_validate_json(canonical) == definition
    changed = _with_signal_exit(
        {
            "when": {
                "all": [
                    {
                        "left": {"indicator": "ema_fast"},
                        "operator": "less_than",
                        "right": {"indicator": "ema_slow"},
                    }
                ]
            }
        }
    )
    assert strategy_fingerprint(StrategyDefinition.model_validate(changed)) != fingerprint


@pytest.mark.parametrize(
    ("signal_exit", "message"),
    [
        (
            {
                "when": {
                    "all": [
                        {
                            "left": {"indicator": "missing"},
                            "operator": "less_than",
                            "right": {"literal": "1"},
                        }
                    ]
                }
            },
            "unknown exits.signal_exit indicator references",
        ),
        (
            {
                "when": {
                    "all": [
                        {
                            "left": {"indicator": "ema_fast", "series": "upper"},
                            "operator": "less_than",
                            "right": {"literal": "1"},
                        }
                    ]
                }
            },
            "operand must omit series",
        ),
        (
            {
                "when": {
                    "all": [
                        {
                            "left": {"indicator": "ema_fast"},
                            "operator": "crosses_below",
                            "right": {"literal": "1"},
                        }
                    ]
                }
            },
            "crossover right operand must reference an indicator",
        ),
        ({"when": _CROSS_BELOW, "order": "taker"}, "Extra inputs are not permitted"),
        ({}, "Field required"),
        ({"when": {"all": []}}, "at least 1 item"),
    ],
)
def test_signal_exit_rejects_what_entry_rejects(signal_exit: object, message: str) -> None:
    """Unknown indicators, bad series, literal crosses, and extra fields fail closed."""
    with pytest.raises(ValidationError, match=message):
        StrategyDefinition.model_validate(_with_signal_exit(signal_exit))


def test_signal_exit_rejects_too_deep_condition_trees() -> None:
    """The bounded entry grammar (depth 4) applies to the exit tree."""
    leaf = {
        "left": {"indicator": "ema_fast"},
        "operator": "less_than",
        "right": {"literal": "1"},
    }
    deep: dict[str, object] = {"all": [leaf]}
    for _level in range(4):
        deep = {"all": [deep]}
    with pytest.raises(ValidationError, match="depth exceeds 4"):
        StrategyDefinition.model_validate(_with_signal_exit({"when": deep}))


def test_signal_exit_requires_series_on_multi_output_indicators() -> None:
    """A multi-series indicator operand must name a declared output, as in entry.when."""
    payload = _with_signal_exit(
        {
            "when": {
                "all": [
                    {
                        "left": {"indicator": "trend_macd", "series": "histogram"},
                        "operator": "less_than",
                        "right": {"literal": "0"},
                    }
                ]
            }
        }
    )
    indicators = cast("list[object]", payload["indicators"])
    indicators.append(
        {
            "id": "trend_macd",
            "kind": "macd",
            "input": "close",
            "parameters": {"fast_period": 12, "slow_period": 26, "signal_period": 9},
        }
    )
    definition = StrategyDefinition.model_validate(payload)
    canonical = json.loads(canonical_strategy_bytes(definition))
    leaf = canonical["exits"]["signal_exit"]["when"]["all"][0]
    assert leaf["left"] == {"indicator": "trend_macd", "series": "histogram"}
    missing_series = _with_signal_exit(
        {
            "when": {
                "all": [
                    {
                        "left": {"indicator": "trend_macd"},
                        "operator": "less_than",
                        "right": {"literal": "0"},
                    }
                ]
            }
        }
    )
    cast("list[object]", missing_series["indicators"]).append(indicators[-1])
    with pytest.raises(ValidationError, match="series must be one of"):
        StrategyDefinition.model_validate(missing_series)


def test_signal_exit_cannot_read_htf_filter_indicators() -> None:
    """The HTF filter gates entries only; an exit rule naming its ids is rejected."""
    payload = _with_signal_exit(
        {
            "when": {
                "all": [
                    {
                        "left": {"indicator": "htf_fast"},
                        "operator": "less_than",
                        "right": {"literal": "1"},
                    }
                ]
            }
        }
    )
    payload["htf_filter"] = {
        "timeframe": "4h",
        "data_requirements": {
            "warmup_bars": 20,
            "required_fields": ["open", "high", "low", "close", "volume"],
        },
        "indicators": [
            {"id": "htf_fast", "kind": "ema", "input": "close", "parameters": {"period": 20}}
        ],
        "when": {
            "all": [
                {
                    "left": {"indicator": "htf_fast"},
                    "operator": "greater_than",
                    "right": {"literal": "1"},
                }
            ]
        },
    }
    with pytest.raises(ValidationError, match="cannot reference HTF filter indicators"):
        StrategyDefinition.model_validate(payload)


def test_summary_names_the_exit_rule_only_when_declared() -> None:
    """The bounded outline reads ``exit when …`` after the entry rule (ADR 0093)."""
    plain = StrategyDefinition.model_validate(_reference_payload())
    assert "exit when" not in strategy_summary(plain)
    declared = StrategyDefinition.model_validate(_with_signal_exit({"when": _CROSS_BELOW}))
    summary = strategy_summary(declared)
    assert " · exit when " in summary
    assert "crosses below" in summary
    assert summary.index("crosses above") < summary.index("exit when")


def test_ema_trend_hold_template_holds_the_trend_with_a_signal_exit() -> None:
    """The template enters on the cross up and exits on the cross down, without a target."""
    template = parse_template_id("ema-trend-hold")
    assert template is StrategyTemplateId.EMA_TREND_HOLD
    assert any(item["id"] == "ema-trend-hold" for item in template_catalog())
    definition = build_template_definition(
        template_id=template,
        strategy_id=UUID("01978a3e-5f2c-7d10-b3a4-000000000092"),
        created_at=datetime(2026, 10, 2, tzinfo=UTC),
        instrument=Instrument(product_id="BTC-USDC", base_currency="BTC", quote_currency="USDC"),
        timeframe="1d",
    )
    assert definition.entry.side == "long"
    exits = definition.exits
    assert exits.take_profit == NoTakeProfit(kind="none")
    assert isinstance(exits.trailing_stop, AtrTrailingStop)
    assert exits.trailing_stop.multiple == "5"
    assert exits.initial_stop.multiple == "3"
    assert exits.time_exit.max_bars_held == 1000
    condition = signal_exit_condition(exits)
    assert condition == AllCondition(
        all=(
            ComparisonCondition(
                left=IndicatorOperand(indicator="fast"),
                operator=ComparisonOperator.CROSSES_BELOW,
                right=IndicatorOperand(indicator="slow"),
            ),
        )
    )
    assert definition.name == "BTC 1d EMA trend hold"
    assert "ema-trend-hold" in definition.metadata.tags
    blueprint = template_blueprint(template)
    assert blueprint["id"] == "ema-trend-hold"
    assert blueprint["indicator_ids"] == ("fast", "slow", "atr")
    assert blueprint["defaults"]["exits.signal_exit"] == "fast crosses below slow"
    assert StrategyDefinition.model_validate_json(canonical_strategy_bytes(definition)) == (
        definition
    )
