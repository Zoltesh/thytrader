"""Deterministic experiential training from attributed local journal evidence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest

from thytrader.memory.evidence import StaticEvidenceResolver
from thytrader.memory.models import (
    ActorOrigin,
    EvidenceKind,
    ExperientialModel,
    ExperientialTrainWrite,
    JournalEntry,
    JournalKind,
    LessonOutcome,
    PatternObservation,
    PatternStatus,
    SentimentLabel,
    SentimentSnapshot,
)
from thytrader.memory.store import InMemoryExperientialMemoryStore
from thytrader.memory.training import (
    ExperientialTrainingError,
    experiential_model_fingerprint,
    train_experiential_model,
)
from thytrader.persistence.audit_events import AuditEventCategory, InMemoryAuditEventStore

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
_BACKTEST = "sha256:" + ("ab" * 32)
_JOURNAL_ID = UUID("11111111-1111-1111-1111-111111111111")
_PATTERN_ID = UUID("22222222-2222-2222-2222-222222222222")
_FADE_ID = UUID("33333333-3333-3333-3333-333333333333")
_ETH_ID = UUID("44444444-4444-4444-4444-444444444444")
_NOTE_ID = UUID("55555555-5555-5555-5555-555555555555")


def _resolver() -> StaticEvidenceResolver:
    """Allow the backtest fingerprint used by these fixtures."""
    return StaticEvidenceResolver(frozenset({(EvidenceKind.BACKTEST, _BACKTEST)}))


def _lesson(*, journal_id: UUID, product_id: str, outcome: LessonOutcome) -> JournalEntry:
    """Build one evidence-backed lesson."""
    return JournalEntry(
        id=journal_id,
        occurred_at=_NOW,
        recorded_at=_NOW,
        origin=ActorOrigin.HUMAN,
        kind=JournalKind.LESSON,
        title="Lesson",
        body="Attributed local evidence.",
        evidence_kind=EvidenceKind.BACKTEST,
        evidence_id=_BACKTEST,
        product_id=product_id,
        lesson_outcome=outcome,
    )


def _pattern(*, pattern_id: UUID, key: str, status: PatternStatus) -> PatternObservation:
    """Build one evidence-backed pattern hook."""
    return PatternObservation(
        id=pattern_id,
        occurred_at=_NOW,
        recorded_at=_NOW,
        origin=ActorOrigin.AGENT,
        pattern_key=key,
        name=key.replace("_", " "),
        hypothesis="Local evidence.",
        status=status,
        evidence_kind=EvidenceKind.BACKTEST,
        evidence_id=_BACKTEST,
    )


async def _train(store: InMemoryExperientialMemoryStore, seed: int = 1) -> ExperientialModel:
    """Train against the in-memory store."""
    return await train_experiential_model(
        store,
        InMemoryAuditEventStore(),
        _resolver(),
        ExperientialTrainWrite(origin=ActorOrigin.AGENT, seed=seed),
        now=_NOW,
    )


def test_train_ranks_attributed_evidence_and_is_idempotent() -> None:
    """Integer ranks are seeded, fingerprintable, and skip unevidenced notes."""
    store = InMemoryExperientialMemoryStore()
    asyncio.run(
        store.append_journal(
            _lesson(journal_id=_JOURNAL_ID, product_id="BTC-USD", outcome=LessonOutcome.SUCCESS)
        )
    )
    asyncio.run(
        store.append_journal(
            _lesson(journal_id=_ETH_ID, product_id="ETH-USD", outcome=LessonOutcome.MISTAKE)
        )
    )
    asyncio.run(
        store.append_journal(
            JournalEntry(
                id=_NOTE_ID,
                occurred_at=_NOW,
                recorded_at=_NOW,
                origin=ActorOrigin.HUMAN,
                kind=JournalKind.NOTE,
                title="Unevidenced",
                body="Must be skipped.",
            )
        )
    )
    asyncio.run(
        store.append_pattern(
            _pattern(pattern_id=_PATTERN_ID, key="morning_gap", status=PatternStatus.SUPPORTED)
        )
    )
    asyncio.run(
        store.append_pattern(
            _pattern(pattern_id=_FADE_ID, key="evening_fade", status=PatternStatus.CONTRADICTED)
        )
    )
    asyncio.run(
        store.append_sentiment(
            SentimentSnapshot(
                occurred_at=_NOW,
                recorded_at=_NOW,
                origin=ActorOrigin.HUMAN,
                label=SentimentLabel.BULLISH,
                product_id="BTC-USD",
                journal_id=_JOURNAL_ID,
            )
        )
    )
    first = asyncio.run(_train(store))
    second = asyncio.run(_train(store))
    assert first.id == second.id
    assert first.fingerprint == experiential_model_fingerprint(seed=1, corpus=first.corpus)
    assert first.advisory.suggested_pattern_keys == ("morning_gap",)
    assert first.advisory.caution_pattern_keys == ("evening_fade",)
    assert first.advisory.suggested_products == ("BTC-USD",)
    assert first.advisory.caution_products == ("ETH-USD",)
    assert "Not a live brain" in first.advisory.notes
    assert str(_NOTE_ID) not in first.corpus.journal_ids
    assert first.corpus.human_rows == 3
    assert first.corpus.agent_rows == 2
    other_seed = asyncio.run(_train(store, seed=2))
    assert other_seed.fingerprint != first.fingerprint


def test_train_fail_closed_on_dangling_or_empty_corpus() -> None:
    """Dangling evidence and an empty evidenced corpus refuse training."""
    store = InMemoryExperientialMemoryStore()
    with pytest.raises(ExperientialTrainingError, match="at least one"):
        asyncio.run(_train(store))
    dangling = JournalEntry(
        occurred_at=_NOW,
        recorded_at=_NOW,
        origin=ActorOrigin.AGENT,
        kind=JournalKind.FACT,
        title="Missing",
        body="Pointer is dangling.",
        evidence_kind=EvidenceKind.BACKTEST,
        evidence_id="missing-fingerprint",
    )
    asyncio.run(store.append_journal(dangling))
    with pytest.raises(ExperientialTrainingError, match="fail-closed"):
        asyncio.run(_train(store))


def test_train_audits_memory_category() -> None:
    """Successful training writes a memory audit event."""
    store = InMemoryExperientialMemoryStore()
    audit = InMemoryAuditEventStore()
    asyncio.run(
        store.append_pattern(
            _pattern(pattern_id=_PATTERN_ID, key="morning_gap", status=PatternStatus.SUPPORTED)
        )
    )
    model = asyncio.run(
        train_experiential_model(
            store,
            audit,
            _resolver(),
            ExperientialTrainWrite(origin=ActorOrigin.HUMAN, seed=1),
            now=_NOW,
        )
    )
    events = asyncio.run(audit.list_recent())
    assert events[0].category is AuditEventCategory.MEMORY
    assert events[0].action == "experiential_model_trained"
    assert model.origin is ActorOrigin.HUMAN
    assert model.engine_id == "thytrader-experiential-train-v1"


def test_experiential_model_accepts_json_zulu_recorded_at() -> None:
    """HTTP dumps use Z; research create-draft must re-validate that payload."""
    store = InMemoryExperientialMemoryStore()
    asyncio.run(
        store.append_pattern(
            _pattern(pattern_id=_PATTERN_ID, key="morning_gap", status=PatternStatus.SUPPORTED)
        )
    )
    model = asyncio.run(_train(store))
    restored = ExperientialModel.model_validate(model.model_dump(mode="json"))
    assert restored.recorded_at.tzinfo is UTC
    assert restored.id == model.id
    assert restored.fingerprint == model.fingerprint
