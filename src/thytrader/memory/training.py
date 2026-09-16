"""Deterministic experiential-model training from attributed local evidence.

The learner is a seeded integer ranker over journals, sentiment, and pattern
hooks. It never bypasses risk, never calls Coinbase, and never interpolates
candles. Output is advisory for research, not a live policy.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import TYPE_CHECKING

from thytrader.execution.ids import utc_now
from thytrader.memory.evidence import ExperientialEvidenceError
from thytrader.memory.models import (
    ADVISORY_SCHEMA_VERSION,
    TRAIN_ENGINE_ID,
    ActorOrigin,
    EvidenceKind,
    ExperientialAdvisory,
    ExperientialCorpus,
    ExperientialModel,
    ExperientialTrainWrite,
    JournalEntry,
    LessonOutcome,
    PatternObservation,
    PatternScore,
    PatternStatus,
    ProductScore,
    SentimentLabel,
    SentimentSnapshot,
)
from thytrader.memory.store import DisabledExperientialMemoryStore, MemoryStoreError
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime
    from uuid import UUID

    from thytrader.memory.evidence import EvidenceResolver
    from thytrader.memory.store import ExperientialMemoryStore

TRAINING_LIMIT = 10_000
_ADVISORY_CAP = 8
_ADVISORY_NOTES = (
    "Advisory only. Does not place orders, bypass risk, call Coinbase, or change "
    "published strategy semantics. Not a live brain."
)
_STATUS_POINTS: dict[PatternStatus, int] = {
    PatternStatus.HYPOTHESIZED: 1,
    PatternStatus.SUPPORTED: 3,
    PatternStatus.CONTRADICTED: -3,
    PatternStatus.RETIRED: 0,
}
_LESSON_POINTS: dict[LessonOutcome, int] = {
    LessonOutcome.NONE: 0,
    LessonOutcome.SUCCESS: 2,
    LessonOutcome.MISTAKE: -2,
    LessonOutcome.MIXED: 0,
}
_SENTIMENT_POINTS: dict[SentimentLabel, int] = {
    SentimentLabel.BULLISH: 1,
    SentimentLabel.BEARISH: -1,
    SentimentLabel.NEUTRAL: 0,
    SentimentLabel.UNKNOWN: 0,
}


class ExperientialTrainingError(ValueError):
    """Refuse training when the corpus is empty, unattributed, or dangling."""


@dataclass
class _PatternAcc:
    """Mutable integer tallies for one pattern_key."""

    score: int = 0
    support_count: int = 0
    contradict_count: int = 0
    name: str = ""


@dataclass
class _ProductAcc:
    """Mutable integer tallies for one product_id."""

    score: int = 0
    success_count: int = 0
    mistake_count: int = 0


class _SelectedCorpus:
    """In-memory training rows that passed origin and evidence checks."""

    def __init__(
        self,
        *,
        journals: tuple[JournalEntry, ...],
        patterns: tuple[PatternObservation, ...],
        sentiment: tuple[SentimentSnapshot, ...],
        evidence_ids: tuple[str, ...],
    ) -> None:
        """Bind selected rows and canonical evidence identities."""
        self.journals = journals
        self.patterns = patterns
        self.sentiment = sentiment
        self.evidence_ids = evidence_ids

    def snapshot(self) -> ExperientialCorpus:
        """Return sorted identities for fingerprinting."""
        human = _origin_count(self.journals, self.patterns, self.sentiment, ActorOrigin.HUMAN)
        agent = _origin_count(self.journals, self.patterns, self.sentiment, ActorOrigin.AGENT)
        return ExperientialCorpus(
            journal_ids=_sorted_ids(item.id for item in self.journals),
            pattern_ids=_sorted_ids(item.id for item in self.patterns),
            sentiment_ids=_sorted_ids(item.id for item in self.sentiment),
            evidence_ids=self.evidence_ids,
            human_rows=human,
            agent_rows=agent,
        )


async def train_experiential_model(
    store: ExperientialMemoryStore,
    audit: AuditEventStore,
    resolver: EvidenceResolver,
    write: ExperientialTrainWrite,
    *,
    now: datetime | None = None,
) -> ExperientialModel:
    """Train one immutable model or return the existing fingerprint match."""
    if isinstance(store, DisabledExperientialMemoryStore):
        raise MemoryStoreError("Experiential memory storage is unavailable.")
    selected = await _select_corpus(store, resolver)
    fingerprint = experiential_model_fingerprint(seed=write.seed, corpus=selected.snapshot())
    existing = await store.get_model_by_fingerprint(fingerprint)
    if existing is not None:
        return existing
    instant = now or utc_now()
    model = _build_model(
        write=write,
        selected=selected,
        fingerprint=fingerprint,
        recorded_at=instant,
    )
    stored = await store.append_model(model)
    await audit.append(
        AuditEvent(
            occurred_at=instant,
            category=AuditEventCategory.MEMORY,
            action="experiential_model_trained",
            outcome=AuditEventOutcome.SUCCESS,
            detail=(
                f"origin={stored.origin.value} fingerprint={stored.fingerprint} "
                f"seed={stored.seed} id={stored.id}"
            ),
        )
    )
    return stored


def experiential_model_fingerprint(*, seed: int, corpus: ExperientialCorpus) -> str:
    """Return the SHA-256 identity of engine, seed, and selected row ids."""
    payload = {
        "engine_id": TRAIN_ENGINE_ID,
        "seed": seed,
        "journal_ids": list(corpus.journal_ids),
        "pattern_ids": list(corpus.pattern_ids),
        "sentiment_ids": list(corpus.sentiment_ids),
        "evidence_ids": list(corpus.evidence_ids),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{sha256(canonical.encode('utf-8')).hexdigest()}"


async def _select_corpus(
    store: ExperientialMemoryStore,
    resolver: EvidenceResolver,
) -> _SelectedCorpus:
    """Keep origin-attributed rows whose evidence pointers resolve locally."""
    journals = await store.list_journals(limit=TRAINING_LIMIT)
    patterns = await store.list_patterns(limit=TRAINING_LIMIT)
    sentiment = await store.list_sentiment(limit=TRAINING_LIMIT)
    selected_journals, journal_evidence = await _select_journals(journals, resolver)
    selected_patterns, pattern_evidence = await _select_patterns(patterns, resolver)
    if not selected_journals and not selected_patterns:
        raise ExperientialTrainingError(
            "training requires at least one origin-attributed journal or pattern "
            "with local evidence"
        )
    journal_ids = {item.id for item in selected_journals}
    selected_sentiment = tuple(
        item for item in sentiment if item.journal_id is not None and item.journal_id in journal_ids
    )
    evidence_ids = tuple(sorted({*journal_evidence, *pattern_evidence}))
    return _SelectedCorpus(
        journals=selected_journals,
        patterns=selected_patterns,
        sentiment=selected_sentiment,
        evidence_ids=evidence_ids,
    )


async def _select_journals(
    rows: tuple[JournalEntry, ...],
    resolver: EvidenceResolver,
) -> tuple[tuple[JournalEntry, ...], tuple[str, ...]]:
    """Drop unevidenced notes; fail closed on dangling pointers."""
    selected: list[JournalEntry] = []
    evidence: list[str] = []
    for row in rows:
        pointer = await _require_local_evidence(row.evidence_kind, row.evidence_id, resolver)
        if pointer is None:
            continue
        selected.append(row)
        evidence.append(pointer)
    return tuple(selected), tuple(evidence)


async def _select_patterns(
    rows: tuple[PatternObservation, ...],
    resolver: EvidenceResolver,
) -> tuple[tuple[PatternObservation, ...], tuple[str, ...]]:
    """Drop unevidenced hooks; fail closed on dangling pointers."""
    selected: list[PatternObservation] = []
    evidence: list[str] = []
    for row in rows:
        pointer = await _require_local_evidence(row.evidence_kind, row.evidence_id, resolver)
        if pointer is None:
            continue
        selected.append(row)
        evidence.append(pointer)
    return tuple(selected), tuple(evidence)


async def _require_local_evidence(
    kind: EvidenceKind,
    evidence_id: str | None,
    resolver: EvidenceResolver,
) -> str | None:
    """Return kind:id when evidence exists, skip none, or refuse dangling ids."""
    if kind is EvidenceKind.NONE:
        return None
    if evidence_id is None or not evidence_id.strip():
        raise ExperientialTrainingError("evidence_id is required when evidence_kind is set")
    try:
        present = await resolver.exists(kind, evidence_id)
    except ExperientialEvidenceError as error:
        raise ExperientialTrainingError(str(error)) from error
    if not present:
        raise ExperientialTrainingError(
            f"training is fail-closed: {kind.value} evidence {evidence_id} was not found locally"
        )
    return f"{kind.value}:{evidence_id}"


def _build_model(
    *,
    write: ExperientialTrainWrite,
    selected: _SelectedCorpus,
    fingerprint: str,
    recorded_at: datetime,
) -> ExperientialModel:
    """Score the selected corpus into an immutable model document."""
    pattern_scores = _pattern_scores(selected, write.seed)
    product_scores = _product_scores(selected, write.seed)
    return ExperientialModel(
        recorded_at=recorded_at,
        origin=write.origin,
        seed=write.seed,
        fingerprint=fingerprint,
        corpus=selected.snapshot(),
        pattern_scores=pattern_scores,
        product_scores=product_scores,
        advisory=_advisory(pattern_scores, product_scores),
    )


def _pattern_scores(selected: _SelectedCorpus, seed: int) -> tuple[PatternScore, ...]:
    """Net integer scores per pattern_key with seeded tie-breaks."""
    totals: dict[str, _PatternAcc] = defaultdict(_PatternAcc)
    for row in selected.patterns:
        slot = totals[row.pattern_key]
        slot.score += _STATUS_POINTS[row.status]
        if row.status is PatternStatus.SUPPORTED:
            slot.support_count += 1
        if row.status is PatternStatus.CONTRADICTED:
            slot.contradict_count += 1
        if not slot.name:
            slot.name = row.name
    ranked = sorted(
        totals.items(),
        key=lambda item: (-item[1].score, _tie_break(seed, item[0]), item[0]),
    )
    return tuple(
        PatternScore(
            pattern_key=key,
            name=values.name or key,
            score=values.score,
            support_count=values.support_count,
            contradict_count=values.contradict_count,
        )
        for key, values in ranked
    )


def _product_scores(selected: _SelectedCorpus, seed: int) -> tuple[ProductScore, ...]:
    """Net integer scores per product from lessons and linked sentiment."""
    totals: dict[str, _ProductAcc] = defaultdict(_ProductAcc)
    for row in selected.journals:
        if row.product_id is None or row.lesson_outcome is LessonOutcome.NONE:
            continue
        slot = totals[row.product_id]
        slot.score += _LESSON_POINTS[row.lesson_outcome]
        if row.lesson_outcome is LessonOutcome.SUCCESS:
            slot.success_count += 1
        if row.lesson_outcome is LessonOutcome.MISTAKE:
            slot.mistake_count += 1
    journal_products = {item.id: item.product_id for item in selected.journals}
    for row in selected.sentiment:
        product_id = row.product_id
        if product_id is None and row.journal_id is not None:
            product_id = journal_products.get(row.journal_id)
        if product_id is None:
            continue
        totals[product_id].score += _SENTIMENT_POINTS[row.label]
    ranked = sorted(
        totals.items(),
        key=lambda item: (-item[1].score, _tie_break(seed, item[0]), item[0]),
    )
    return tuple(
        ProductScore(
            product_id=key,
            score=values.score,
            success_count=values.success_count,
            mistake_count=values.mistake_count,
        )
        for key, values in ranked
    )


def _advisory(
    pattern_scores: tuple[PatternScore, ...],
    product_scores: tuple[ProductScore, ...],
) -> ExperientialAdvisory:
    """Split positive and negative ranks into a bounded research hint."""
    suggested_patterns = tuple(item.pattern_key for item in pattern_scores if item.score > 0)[
        :_ADVISORY_CAP
    ]
    caution_patterns = tuple(item.pattern_key for item in pattern_scores if item.score < 0)[
        :_ADVISORY_CAP
    ]
    suggested_products = tuple(item.product_id for item in product_scores if item.score > 0)[
        :_ADVISORY_CAP
    ]
    caution_products = tuple(item.product_id for item in product_scores if item.score < 0)[
        :_ADVISORY_CAP
    ]
    return ExperientialAdvisory(
        schema_version=ADVISORY_SCHEMA_VERSION,
        suggested_pattern_keys=suggested_patterns,
        caution_pattern_keys=caution_patterns,
        suggested_products=suggested_products,
        caution_products=caution_products,
        notes=_ADVISORY_NOTES,
    )


def _tie_break(seed: int, key: str) -> str:
    """Order equal scores by a seeded digest so tests can pin ranking."""
    return sha256(f"{seed}:{key}".encode()).hexdigest()


def _sorted_ids(values: Iterable[UUID]) -> tuple[str, ...]:
    """Canonical string UUID order."""
    return tuple(sorted(str(item) for item in values))


def _origin_count(
    journals: tuple[JournalEntry, ...],
    patterns: tuple[PatternObservation, ...],
    sentiment: tuple[SentimentSnapshot, ...],
    origin: ActorOrigin,
) -> int:
    """Count selected rows authored by one origin."""
    return (
        sum(1 for item in journals if item.origin is origin)
        + sum(1 for item in patterns if item.origin is origin)
        + sum(1 for item in sentiment if item.origin is origin)
    )
