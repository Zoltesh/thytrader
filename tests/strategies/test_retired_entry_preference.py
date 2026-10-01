"""``execution.entry_preference = "marketable_limit"`` is retired: rejected on save."""

from __future__ import annotations

from datetime import UTC, datetime

from thytrader.execution.ids import uuid7
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import (
    RETIRED_ENTRY_PREFERENCE_MESSAGE,
    authoring_issues,
    evaluate_document,
    parse_document_text,
)
from thytrader.strategies.models import (
    StrategyDefinition,
    canonical_strategy_bytes,
    strategy_fingerprint,
)

_NOW = datetime(2026, 10, 1, tzinfo=UTC)


def _legacy_definition() -> StrategyDefinition:
    """Return a valid template definition carrying the retired entry preference."""
    payload = create_template_strategy(now=_NOW).model_dump(mode="python")
    payload["execution"]["entry_preference"] = "marketable_limit"
    return StrategyDefinition.model_validate(payload)


def test_saving_marketable_limit_is_a_validation_error() -> None:
    """The library stores the document invalid, with an issue at the field, and no fingerprint."""
    definition = _legacy_definition()
    document = parse_document_text(canonical_strategy_bytes(definition).decode("utf-8"))
    evaluated = evaluate_document(document, strategy_id=uuid7(_NOW), created_at=_NOW)
    assert evaluated.definition is None
    assert evaluated.fingerprint is None
    assert [(issue.loc, issue.message) for issue in evaluated.validation.issues] == [
        ("execution.entry_preference", RETIRED_ENTRY_PREFERENCE_MESSAGE)
    ]
    assert evaluated.document["execution"] == document["execution"]


def test_maker_only_stays_valid_and_legacy_snapshots_still_verify() -> None:
    """Maker-only saves cleanly; a persisted legacy snapshot still parses byte-for-byte."""
    maker = create_template_strategy(now=_NOW)
    assert authoring_issues(maker) == ()
    legacy = _legacy_definition()
    canonical = canonical_strategy_bytes(legacy).decode("utf-8")
    reparsed = StrategyDefinition.model_validate_json(canonical)
    assert canonical_strategy_bytes(reparsed).decode("utf-8") == canonical
    assert strategy_fingerprint(reparsed) == strategy_fingerprint(legacy)
    assert authoring_issues(reparsed)
