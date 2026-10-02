"""Read-only reference instruments in the strategy schema (ADR 0096).

Covers validation (quote currency, timeframe pairing, sources, bounds), canonical
identity (reference-free documents keep their bytes; a reference document is pinned
by a golden), derived per-reference warmup, the library summary, and the
``btc-regime-gate`` template.
"""

from __future__ import annotations

from collections.abc import Callable
import copy
from datetime import UTC, datetime
import json
from typing import TYPE_CHECKING, cast
from uuid import UUID

from pydantic import ValidationError
import pytest

from tests.strategies.reference_support import GOLDEN, reference_payload, reference_strategy
from thytrader.market_data.products import parse_spot_product_id
from thytrader.strategies.models import (
    MAX_REFERENCE_INSTRUMENTS,
    Instrument,
    StrategyDefinition,
    canonical_strategy_bytes,
    decision_clock_indicators,
    is_valid_reference_pair,
    reference_data_requirements,
    reference_indicator_groups,
    reference_series,
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

if TYPE_CHECKING:
    from thytrader.market_data.models import DatasetTimeframe

_GOLDEN_FINGERPRINT = "sha256:e4a7ba1efdbf3c270a3edd830c9a07d5df1f792ecfa56324dd6630dde0bad383"
_REFERENCE_FREE_GOLDENS = (
    "reference_strategy_v1.json",
    "sma_strategy_v1.json",
    "volume_sma_strategy_v1.json",
    "nested_condition_strategy_v1.json",
)

Mutation = Callable[[dict[str, object]], None]


def _references(payload: dict[str, object]) -> list[dict[str, object]]:
    """Return the mutable reference list of one payload."""
    requirements = cast("dict[str, object]", payload["data_requirements"])
    return cast("list[dict[str, object]]", requirements["reference_instruments"])


def _indicators(payload: dict[str, object]) -> list[dict[str, object]]:
    """Return the mutable indicator list of one payload."""
    return cast("list[dict[str, object]]", payload["indicators"])


def _rejected(mutate: Mutation) -> str:
    """Apply one mutation to the fixture and return the first validation message."""
    payload = copy.deepcopy(reference_payload())
    mutate(payload)
    with pytest.raises(ValidationError) as caught:
        StrategyDefinition.model_validate(payload)
    return str(caught.value.errors()[0]["msg"])


@pytest.mark.parametrize("name", _REFERENCE_FREE_GOLDENS)
def test_reference_free_documents_keep_their_canonical_bytes(name: str) -> None:
    """Documents without references never gain a reference_instruments key."""
    raw = (GOLDEN / name).read_bytes()
    definition = StrategyDefinition.model_validate_json(raw)
    assert canonical_strategy_bytes(definition) == raw
    assert b"reference_instruments" not in raw
    assert b'"source"' not in raw


def test_an_explicit_empty_reference_list_is_byte_identical_to_omission() -> None:
    """``reference_instruments: []`` and ``source: null`` canonicalize away."""
    raw = (GOLDEN / "reference_strategy_v1.json").read_bytes()
    payload = json.loads(raw)
    payload["data_requirements"]["reference_instruments"] = []
    for indicator in payload["indicators"]:
        indicator["source"] = None
    definition = StrategyDefinition.model_validate(payload)
    assert canonical_strategy_bytes(definition) == raw


def test_reference_document_canonical_bytes_and_fingerprint_are_pinned() -> None:
    """The reference fixture serializes to the golden bytes and fingerprint."""
    definition = reference_strategy()
    golden = (GOLDEN / "reference_instrument_strategy_v1.json").read_bytes()
    assert canonical_strategy_bytes(definition) == golden
    assert strategy_fingerprint(definition) == _GOLDEN_FINGERPRINT
    assert StrategyDefinition.model_validate_json(golden) == definition


def test_fingerprint_covers_the_reference_series() -> None:
    """Changing the reference product or timeframe changes the snapshot identity."""
    base = strategy_fingerprint(reference_strategy())
    assert strategy_fingerprint(reference_strategy(reference_product="SOL-USD")) != base
    assert strategy_fingerprint(reference_strategy(reference_timeframe="4h")) != base


def test_sourced_indicators_leave_the_decision_clock_and_derive_reference_warmup() -> None:
    """Reference indicators group under their reference with derived warmup and fields."""
    definition = reference_strategy()
    assert [item.id for item in decision_clock_indicators(definition)] == ["atr"]
    groups = reference_indicator_groups(definition)
    assert [(reference.id, [item.id for item in items]) for reference, items in groups] == [
        ("btc", ["btc_close", "btc_sma"])
    ]
    (requirement,) = reference_data_requirements(definition)
    assert requirement.reference_id == "btc"
    assert requirement.product_id == "BTC-USD"
    assert requirement.timeframe == "1d"
    assert requirement.warmup_bars == 2
    assert requirement.required_fields == ("close",)
    assert reference_series(definition) == frozenset({("BTC-USD", "1d")})


def test_reference_warmup_includes_offset_and_is_independent_of_decision_warmup() -> None:
    """A lagged EMA(100) on the reference needs 101 reference bars; decision warmup stays 2."""
    payload = reference_payload()
    _indicators(payload)[2] = {
        "id": "btc_sma",
        "kind": "ema",
        "input": "close",
        "parameters": {"period": 100},
        "offset": 1,
        "source": "btc",
    }
    definition = StrategyDefinition.model_validate(payload)
    assert definition.data_requirements.warmup_bars == 2
    assert reference_data_requirements(definition)[0].warmup_bars == 101


@pytest.mark.parametrize(
    ("decision", "reference", "valid"),
    [
        ("1h", "1h", True),
        ("1h", "1d", True),
        ("4h", "1d", True),
        ("1m", "1d", True),
        ("1h", "15m", False),
        ("4h", "6h", False),
        ("1d", "1d", True),
    ],
)
def test_reference_timeframe_pairing(decision: str, reference: str, valid: bool) -> None:
    """A reference clock equals the decision clock or is a coarser integer multiple."""
    assert is_valid_reference_pair(decision, reference) is valid


def test_same_timeframe_and_traded_product_references_are_valid() -> None:
    """BTC may reference its own 1h bars, and a 1h strategy may read a 1h reference."""
    same_clock = reference_strategy(reference_timeframe="1h")
    assert reference_data_requirements(same_clock)[0].timeframe == "1h"
    own = reference_strategy(product_id="BTC-USD")
    assert reference_series(own) == frozenset({("BTC-USD", "1d")})


def _set_reference(key: str, value: object) -> Mutation:
    """Mutation that sets one key on the fixture's only reference."""

    def mutate(payload: dict[str, object]) -> None:
        _references(payload)[0][key] = value

    return mutate


def _set_indicator(index: int, key: str, value: object) -> Mutation:
    """Mutation that sets one key on one fixture indicator."""

    def mutate(payload: dict[str, object]) -> None:
        _indicators(payload)[index][key] = value

    return mutate


def _add_reference(reference: dict[str, object]) -> Mutation:
    """Mutation that appends one reference (unused unless an indicator reads it)."""

    def mutate(payload: dict[str, object]) -> None:
        _references(payload).append(reference)

    return mutate


def _too_many(payload: dict[str, object]) -> None:
    """Declare one reference more than the bound, each read by an indicator."""
    references = _references(payload)
    for index, product in enumerate(("SOL-USD", "ADA-USD", "XRP-USD")):
        references.append({"id": f"ref{index}", "product_id": product, "timeframe": "1d"})
        _indicators(payload).append(
            {
                "id": f"ref{index}_close",
                "kind": "identity",
                "input": "close",
                "parameters": {},
                "source": f"ref{index}",
            }
        )


def _constant_with_source(payload: dict[str, object]) -> None:
    """Give a constant indicator a reference source."""
    _indicators(payload).append(
        {"id": "level", "kind": "constant", "parameters": {"value": "1"}, "source": "btc"}
    )


def _sourced_stop(payload: dict[str, object]) -> None:
    """Point the initial stop at an ATR that reads the reference."""
    _indicators(payload).append(
        {
            "id": "btc_atr",
            "kind": "atr",
            "input": ["high", "low", "close"],
            "parameters": {"period": 2},
            "source": "btc",
        }
    )
    exits = cast("dict[str, object]", payload["exits"])
    exits["initial_stop"] = {"kind": "atr_multiple", "atr_indicator": "btc_atr", "multiple": "2"}


def _htf_with_source(payload: dict[str, object]) -> None:
    """Add an HTF filter whose indicator reads the reference."""
    payload["htf_filter"] = {
        "timeframe": "4h",
        "data_requirements": {"warmup_bars": 2, "required_fields": ["close"]},
        "indicators": [
            {
                "id": "htf_close",
                "kind": "identity",
                "input": "close",
                "parameters": {},
                "source": "btc",
            }
        ],
        "when": {
            "all": [
                {
                    "left": {"indicator": "htf_close"},
                    "operator": "greater_than",
                    "right": {"literal": "1"},
                }
            ]
        },
    }


def _htf_with_references(payload: dict[str, object]) -> None:
    """Declare references inside the HTF filter's data requirements."""
    payload["htf_filter"] = {
        "timeframe": "4h",
        "data_requirements": {
            "warmup_bars": 2,
            "required_fields": ["close"],
            "reference_instruments": [{"id": "eth4h", "product_id": "ETH-USD", "timeframe": "4h"}],
        },
        "indicators": [{"id": "htf_close", "kind": "identity", "input": "close", "parameters": {}}],
        "when": {
            "all": [
                {
                    "left": {"indicator": "htf_close"},
                    "operator": "greater_than",
                    "right": {"literal": "1"},
                }
            ]
        },
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_set_reference("product_id", "BTC-USDC"), "must use the strategy quote currency USD"),
        (_set_reference("timeframe", "15m"), "or be a coarser integer multiple"),
        (_set_reference("product_id", "BTC-EUR"), "String should match pattern"),
        (_set_reference("id", "BTC"), "String should match pattern"),
        (_set_indicator(1, "source", "eth"), "must name a declared"),
        (_set_indicator(1, "timeframe", "1d"), "must omit timeframe"),
        (
            _add_reference({"id": "sol", "product_id": "SOL-USD", "timeframe": "1d"}),
            "must be read by at least one indicator",
        ),
        (
            _add_reference({"id": "btc", "product_id": "SOL-USD", "timeframe": "1d"}),
            "ids must be unique",
        ),
        (
            _add_reference({"id": "btc2", "product_id": "BTC-USD", "timeframe": "1d"}),
            "must not repeat one product_id and timeframe pair",
        ),
        (_too_many, f"at most {MAX_REFERENCE_INSTRUMENTS} items"),
        (_constant_with_source, "constant must omit source"),
        (_sourced_stop, "must read the traded instrument"),
        (_htf_with_source, "HTF indicators must omit source"),
        (_htf_with_references, "must not declare reference_instruments"),
    ],
)
def test_invalid_reference_documents_fail_closed(mutate: Mutation, message: str) -> None:
    """Every reference rule rejects the document with an actionable message."""
    assert message in _rejected(mutate)


def test_strategy_timeframe_coarser_than_reference_is_rejected() -> None:
    """A 1d strategy cannot read a 4h reference (reference must be equal or coarser)."""
    payload = reference_payload(timeframe="1d", reference_timeframe="4h")
    with pytest.raises(ValidationError, match="coarser integer multiple"):
        StrategyDefinition.model_validate(payload)


def test_summary_names_the_reference_operands_and_series() -> None:
    """The library outline reads ``BTC · close [1d] > BTC · SMA(2) [1d]`` and names the series."""
    summary = strategy_summary(reference_strategy())
    assert "BTC · close [1d] > BTC · SMA(2) [1d]" in summary
    assert "reads BTC-USD 1d" in summary
    assert summary.startswith("ETH-USD · 1h · ")


def test_reference_free_summaries_are_unchanged() -> None:
    """A document without references never mentions a reference series."""
    raw = (GOLDEN / "reference_strategy_v1.json").read_bytes()
    summary = strategy_summary(StrategyDefinition.model_validate_json(raw))
    assert "reads " not in summary
    assert " · " in summary


def _template(product_id: str, timeframe: DatasetTimeframe = "4h") -> StrategyDefinition:
    """Build the btc-regime-gate template on one product."""
    base, quote = parse_spot_product_id(product_id)
    return build_template_definition(
        template_id=StrategyTemplateId.BTC_REGIME_GATE,
        strategy_id=UUID("01985cf0-7b60-7000-8000-000000000095"),
        created_at=datetime(2026, 10, 2, tzinfo=UTC),
        instrument=Instrument(product_id=product_id, base_currency=base, quote_currency=quote),
        timeframe=timeframe,
    )


def test_btc_regime_gate_template_reads_btc_in_the_instrument_quote() -> None:
    """The template gates an EMA trend on BTC-<quote> 1d close > EMA(100)."""
    definition = _template("ETH-USDC")
    (reference,) = definition.data_requirements.reference_instruments
    assert (reference.id, reference.product_id, reference.timeframe) == ("btc", "BTC-USDC", "1d")
    assert _template("SOL-USD").data_requirements.reference_instruments[0].product_id == "BTC-USD"
    (requirement,) = reference_data_requirements(definition)
    assert requirement.warmup_bars == 100
    assert [item.id for item in decision_clock_indicators(definition)] == ["fast", "slow", "atr"]
    summary = strategy_summary(definition)
    assert "BTC · close [1d] > BTC · EMA(100) [1d]" in summary
    assert "reads BTC-USDC 1d" in summary
    assert "btc-regime-gate" in definition.metadata.tags


@pytest.mark.parametrize("timeframe", ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d"])
def test_btc_regime_gate_template_is_valid_on_every_venue_clock(
    timeframe: DatasetTimeframe,
) -> None:
    """1d divides every venue clock, so the template validates on each one (and on BTC itself)."""
    assert _template("ETH-USDC", timeframe).timeframe == timeframe
    assert _template("BTC-USDC", timeframe).instrument.product_id == "BTC-USDC"


def test_btc_regime_gate_is_listed_with_a_blueprint() -> None:
    """list-templates/show-template discover the template, its reference, and its axes."""
    assert parse_template_id("btc-regime-gate") is StrategyTemplateId.BTC_REGIME_GATE
    listed = {item["id"]: item for item in template_catalog()}
    assert "BTC" in listed["btc-regime-gate"]["description"]
    blueprint = template_blueprint(StrategyTemplateId.BTC_REGIME_GATE)
    assert blueprint["id"] == "btc-regime-gate"
    assert "btc_ema" in blueprint["indicator_ids"]
    assert blueprint["reference_instruments"][0]["timeframe"] == "1d"
    axes = {axis.get("indicator_id") for axis in blueprint["sweepable_axes"]}
    assert {"fast", "slow", "btc_ema"} <= axes
