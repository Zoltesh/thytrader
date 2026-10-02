"""Validation issues use document paths and plain messages, never Pydantic member tags."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
import json
from typing import cast

from pydantic import ValidationError
import pytest

from thytrader.execution.ids import uuid7
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.issue_paths import DocumentIssue, document_issues, render_path
from thytrader.strategies.library import evaluate_document, parse_document
from thytrader.strategies.models import StrategyDefinition

_NOW = datetime(2026, 10, 2, tzinfo=UTC)


def _document() -> dict[str, object]:
    """Return one valid EMA-trend template document as plain JSON values."""
    definition = create_template_strategy(now=_NOW)
    return cast("dict[str, object]", json.loads(definition.model_dump_json(by_alias=True)))


def _object(value: object) -> dict[str, object]:
    """Narrow one JSON value to a mutable object (fails the test otherwise)."""
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def _array(value: object) -> list[object]:
    """Narrow one JSON value to a mutable array (fails the test otherwise)."""
    assert isinstance(value, list)
    return cast("list[object]", value)


def _node(document: dict[str, object], *path: str | int) -> dict[str, object]:
    """Walk ``path`` through nested objects and arrays and return the object there."""
    node: object = document
    for part in path:
        node = _array(node)[part] if isinstance(part, int) else _object(node)[part]
    return _object(node)


def _issues(document: dict[str, object]) -> list[tuple[str, str]]:
    """Validate ``document`` and return its rendered (loc, message) issues."""
    with pytest.raises(ValidationError) as caught:
        StrategyDefinition.model_validate(document)
    return [
        (issue.loc, issue.message) for issue in document_issues(caught.value, document, limit=50)
    ]


def _first_comparison(document: dict[str, object]) -> dict[str, object]:
    """Return the first comparison of the template's ALL entry rule for in-place edits."""
    return _node(document, "entry", "when", "all", 0)


def test_unknown_operand_key_is_reported_at_its_document_path() -> None:
    """``left.input`` is named by path, and the operand alternatives are spelled out."""
    document = _document()
    _first_comparison(document)["left"] = {"input": "fast"}
    assert _issues(document) == [
        ("entry.when.all[0].left.input", 'unknown field "input"'),
        (
            "entry.when.all[0].left",
            'must be an indicator operand (needs "indicator") or a literal operand '
            '(needs "literal")',
        ),
    ]


def test_no_issue_location_carries_pydantic_member_tags() -> None:
    """None of the noisy union-member errors survive as separate issues."""
    document = _document()
    _first_comparison(document)["left"] = {"input": "fast"}
    for loc, message in _issues(document):
        assert "Condition" not in loc
        assert "Operand" not in loc
        assert "function-after" not in loc
        assert "Extra inputs" not in message
        assert "Field required" not in message


def test_model_validator_message_drops_the_value_error_prefix() -> None:
    """A crossover against a literal reports the comparison and the plain reason."""
    document = _document()
    _first_comparison(document)["left"] = {"literal": "5"}
    assert _issues(document) == [
        ("entry.when.all[0]", "crossover left operand must reference an indicator")
    ]


def test_discriminated_union_paths_drop_the_tag_value() -> None:
    """``exits.take_profit.reward_risk.multiple`` becomes ``exits.take_profit.multiple``."""
    document = _document()
    exits = _node(document, "exits")
    exits["take_profit"] = {"kind": "reward_risk"}
    assert _issues(document) == [("exits.take_profit.multiple", '"multiple" is required')]
    exits["take_profit"] = {"kind": "bogus"}
    assert _issues(document) == [
        ("exits.take_profit.kind", "must be one of 'reward_risk', 'none' (got 'bogus')")
    ]


def test_boolean_discriminator_tags_are_dropped() -> None:
    """The trailing stop's ``enabled`` discriminator appears in Pydantic locs as ``1``."""
    document = _document()
    exits = _node(document, "exits")
    exits["trailing_stop"] = {"enabled": True}
    assert [loc for loc, _message in _issues(document)] == [
        "exits.trailing_stop.kind",
        "exits.trailing_stop.atr_indicator",
        "exits.trailing_stop.multiple",
    ]


def test_bad_enum_and_parameter_values_keep_one_issue_each() -> None:
    """The best-matching member's complaint is kept; every other member's is dropped."""
    document = _document()
    _node(document, "entry", "when", "all", 1)["operator"] = "gt"
    _node(document, "indicators", 0)["parameters"] = {"period": "x"}
    issues = _issues(document)
    assert [loc for loc, _message in issues] == [
        "indicators[0].parameters.period",
        "entry.when.all[1].operator",
    ]
    assert issues[0][1] == "must be a valid integer, unable to parse string as an integer"
    assert issues[1][1].startswith("must be 'greater_than'")


def test_group_typos_and_scalars_explain_the_allowed_shapes() -> None:
    """A misspelled group key and a scalar operand name every shape the value may take."""
    document = _document()
    entry = _node(document, "entry")
    original = copy.deepcopy(entry["when"])
    entry["when"] = {"alll": []}
    assert _issues(document) == [
        ("entry.when.alll", 'unknown field "alll"'),
        (
            "entry.when",
            'must be an all condition (needs "all") or an any condition (needs "any") or '
            'a not condition (needs "not")',
        ),
    ]
    entry["when"] = original
    _first_comparison(document)["left"] = "fast"
    assert _issues(document) == [
        (
            "entry.when.all[0].left",
            "must be a JSON object: an indicator operand or a literal operand",
        )
    ]


def test_mixed_operand_fields_say_which_member_rejects_which_key() -> None:
    """Keys valid on one member and unknown on another are not called unknown."""
    document = _document()
    _first_comparison(document)["left"] = {"indicator": "fast", "literal": "5"}
    assert _issues(document) == [
        (
            "entry.when.all[0].left",
            'must be an indicator operand (does not take "literal") or a literal operand '
            '(does not take "indicator")',
        )
    ]


def test_saved_invalid_documents_store_document_paths() -> None:
    """The library (HTTP, CLI, and UI read these) stores the rewritten issues."""
    document = _document()
    _first_comparison(document)["left"] = {"input": "fast"}
    evaluated = evaluate_document(
        parse_document(document), strategy_id=uuid7(_NOW), created_at=_NOW
    )
    assert evaluated.definition is None
    assert evaluated.validation.issues[0].loc == "entry.when.all[0].left.input"
    assert evaluated.validation.issues[0].message == 'unknown field "input"'


def test_render_path_formats_indices_and_the_root() -> None:
    """Indices render in brackets and an empty location names the document."""
    assert render_path(("entry", "when", "all", 0, "left")) == "entry.when.all[0].left"
    assert render_path(()) == "(document)"
    assert DocumentIssue(loc="a", message="b") == DocumentIssue(loc="a", message="b")


def test_issue_limit_is_respected() -> None:
    """The caller's cap bounds the issue list."""
    document = _document()
    _first_comparison(document)["left"] = {"input": "fast"}
    with pytest.raises(ValidationError) as caught:
        StrategyDefinition.model_validate(document)
    assert len(document_issues(caught.value, document, limit=1)) == 1
