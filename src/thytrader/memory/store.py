"""Append-only experiential-memory persistence contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.memory.models import (
    ActorOrigin,
    ExperientialModel,
    JournalEntry,
    JournalKind,
    MemoryCounts,
    NotificationRecord,
    PatternObservation,
    SentimentSnapshot,
)

if TYPE_CHECKING:
    from uuid import UUID

_MemoryRow = JournalEntry | SentimentSnapshot | PatternObservation | NotificationRecord
_MODEL_LIST_CAP = 100
_TRAINING_LIST_CAP = 10_000


class MemoryStoreError(RuntimeError):
    """Signal that durable experiential memory cannot complete a mutation."""


@runtime_checkable
class ExperientialMemoryStore(Protocol):
    """Append and query origin-attributed journals, sentiment, patterns, and notify rows."""

    async def append_journal(self, entry: JournalEntry) -> JournalEntry:
        """Persist one journal row."""
        ...

    async def list_journals(
        self,
        *,
        origin: ActorOrigin | None = None,
        kind: JournalKind | None = None,
        limit: int = 50,
    ) -> tuple[JournalEntry, ...]:
        """Return newest-first journals, optionally filtered."""
        ...

    async def append_sentiment(self, snapshot: SentimentSnapshot) -> SentimentSnapshot:
        """Persist one sentiment snapshot."""
        ...

    async def list_sentiment(
        self,
        *,
        origin: ActorOrigin | None = None,
        limit: int = 50,
    ) -> tuple[SentimentSnapshot, ...]:
        """Return newest-first sentiment snapshots."""
        ...

    async def append_pattern(self, observation: PatternObservation) -> PatternObservation:
        """Persist one pattern-learning observation."""
        ...

    async def list_patterns(
        self,
        *,
        origin: ActorOrigin | None = None,
        pattern_key: str | None = None,
        limit: int = 50,
    ) -> tuple[PatternObservation, ...]:
        """Return newest-first pattern observations."""
        ...

    async def append_notification(self, record: NotificationRecord) -> NotificationRecord:
        """Persist one notification attempt."""
        ...

    async def list_notifications(
        self,
        *,
        origin: ActorOrigin | None = None,
        limit: int = 50,
    ) -> tuple[NotificationRecord, ...]:
        """Return newest-first notification attempts."""
        ...

    async def counts(self) -> MemoryCounts:
        """Return row counts for operator/memory status."""
        ...

    async def append_model(self, model: ExperientialModel) -> ExperientialModel:
        """Persist one trained experiential model, or return the fingerprint match."""
        ...

    async def get_model(self, model_id: UUID) -> ExperientialModel | None:
        """Load one trained model by id."""
        ...

    async def get_model_by_fingerprint(self, fingerprint: str) -> ExperientialModel | None:
        """Load one trained model by content identity."""
        ...

    async def list_models(self, *, limit: int = 50) -> tuple[ExperientialModel, ...]:
        """Return newest-first trained models."""
        ...


class DisabledExperientialMemoryStore:
    """Refuse writes when PostgreSQL is unconfigured; reads stay empty."""

    async def append_journal(self, entry: JournalEntry) -> JournalEntry:
        """Refuse journal writes without durable storage."""
        del entry
        raise MemoryStoreError("Experiential memory storage is unavailable.")

    async def list_journals(
        self,
        *,
        origin: ActorOrigin | None = None,
        kind: JournalKind | None = None,
        limit: int = 50,
    ) -> tuple[JournalEntry, ...]:
        """Return no journals when storage is unconfigured."""
        del origin, kind, limit
        return ()

    async def append_sentiment(self, snapshot: SentimentSnapshot) -> SentimentSnapshot:
        """Refuse sentiment writes without durable storage."""
        del snapshot
        raise MemoryStoreError("Experiential memory storage is unavailable.")

    async def list_sentiment(
        self,
        *,
        origin: ActorOrigin | None = None,
        limit: int = 50,
    ) -> tuple[SentimentSnapshot, ...]:
        """Return no sentiment rows when storage is unconfigured."""
        del origin, limit
        return ()

    async def append_pattern(self, observation: PatternObservation) -> PatternObservation:
        """Refuse pattern writes without durable storage."""
        del observation
        raise MemoryStoreError("Experiential memory storage is unavailable.")

    async def list_patterns(
        self,
        *,
        origin: ActorOrigin | None = None,
        pattern_key: str | None = None,
        limit: int = 50,
    ) -> tuple[PatternObservation, ...]:
        """Return no pattern rows when storage is unconfigured."""
        del origin, pattern_key, limit
        return ()

    async def append_notification(self, record: NotificationRecord) -> NotificationRecord:
        """Refuse notification writes without durable storage."""
        del record
        raise MemoryStoreError("Experiential memory storage is unavailable.")

    async def list_notifications(
        self,
        *,
        origin: ActorOrigin | None = None,
        limit: int = 50,
    ) -> tuple[NotificationRecord, ...]:
        """Return no notifications when storage is unconfigured."""
        del origin, limit
        return ()

    async def counts(self) -> MemoryCounts:
        """Return zeros when storage is unconfigured."""
        return MemoryCounts(journals=0, sentiment=0, patterns=0, notifications=0)

    async def append_model(self, model: ExperientialModel) -> ExperientialModel:
        """Refuse model writes without durable storage."""
        del model
        raise MemoryStoreError("Experiential memory storage is unavailable.")

    async def get_model(self, model_id: UUID) -> ExperientialModel | None:
        """Return no models when storage is unconfigured."""
        del model_id
        return None

    async def get_model_by_fingerprint(self, fingerprint: str) -> ExperientialModel | None:
        """Return no models when storage is unconfigured."""
        del fingerprint
        return None

    async def list_models(self, *, limit: int = 50) -> tuple[ExperientialModel, ...]:
        """Return no models when storage is unconfigured."""
        del limit
        return ()


class InMemoryExperientialMemoryStore:
    """Retain experiential rows in process memory for tests."""

    def __init__(self) -> None:
        """Start with empty append-only lists."""
        self.journals: list[JournalEntry] = []
        self.sentiment: list[SentimentSnapshot] = []
        self.patterns: list[PatternObservation] = []
        self.notifications: list[NotificationRecord] = []
        self.models: list[ExperientialModel] = []

    async def append_journal(self, entry: JournalEntry) -> JournalEntry:
        """Store one journal row."""
        self.journals.append(entry)
        return entry

    async def list_journals(
        self,
        *,
        origin: ActorOrigin | None = None,
        kind: JournalKind | None = None,
        limit: int = 50,
    ) -> tuple[JournalEntry, ...]:
        """Return newest-first journals, optionally filtered."""
        rows = [
            item
            for item in self.journals
            if (origin is None or item.origin is origin) and (kind is None or item.kind is kind)
        ]
        return _newest(rows, limit)

    async def append_sentiment(self, snapshot: SentimentSnapshot) -> SentimentSnapshot:
        """Store one sentiment snapshot."""
        self.sentiment.append(snapshot)
        return snapshot

    async def list_sentiment(
        self,
        *,
        origin: ActorOrigin | None = None,
        limit: int = 50,
    ) -> tuple[SentimentSnapshot, ...]:
        """Return newest-first sentiment snapshots."""
        rows = [item for item in self.sentiment if origin is None or item.origin is origin]
        return _newest(rows, limit)

    async def append_pattern(self, observation: PatternObservation) -> PatternObservation:
        """Store one pattern observation."""
        self.patterns.append(observation)
        return observation

    async def list_patterns(
        self,
        *,
        origin: ActorOrigin | None = None,
        pattern_key: str | None = None,
        limit: int = 50,
    ) -> tuple[PatternObservation, ...]:
        """Return newest-first pattern observations."""
        rows = [
            item
            for item in self.patterns
            if (origin is None or item.origin is origin)
            and (pattern_key is None or item.pattern_key == pattern_key)
        ]
        return _newest(rows, limit)

    async def append_notification(self, record: NotificationRecord) -> NotificationRecord:
        """Store one notification attempt."""
        self.notifications.append(record)
        return record

    async def list_notifications(
        self,
        *,
        origin: ActorOrigin | None = None,
        limit: int = 50,
    ) -> tuple[NotificationRecord, ...]:
        """Return newest-first notification attempts."""
        rows = [item for item in self.notifications if origin is None or item.origin is origin]
        return _newest(rows, limit)

    async def counts(self) -> MemoryCounts:
        """Return in-memory row counts."""
        return MemoryCounts(
            journals=len(self.journals),
            sentiment=len(self.sentiment),
            patterns=len(self.patterns),
            notifications=len(self.notifications),
        )

    async def append_model(self, model: ExperientialModel) -> ExperientialModel:
        """Store one trained model, returning an existing fingerprint match."""
        existing = await self.get_model_by_fingerprint(model.fingerprint)
        if existing is not None:
            return existing
        self.models.append(model)
        return model

    async def get_model(self, model_id: UUID) -> ExperientialModel | None:
        """Load one trained model by id."""
        return next((item for item in self.models if item.id == model_id), None)

    async def get_model_by_fingerprint(self, fingerprint: str) -> ExperientialModel | None:
        """Load one trained model by content identity."""
        return next(
            (item for item in self.models if item.fingerprint == fingerprint),
            None,
        )

    async def list_models(self, *, limit: int = 50) -> tuple[ExperientialModel, ...]:
        """Return newest-first trained models."""
        if limit < 1:
            raise ValueError("limit must be positive")
        bounded = min(limit, _MODEL_LIST_CAP)
        ordered = sorted(self.models, key=lambda item: (item.recorded_at, item.id), reverse=True)
        return tuple(ordered[:bounded])


def _newest[T: _MemoryRow](rows: list[T], limit: int) -> tuple[T, ...]:
    """Return newest-first rows using occurred_at then id."""
    if limit < 1:
        raise ValueError("limit must be positive")
    bounded = min(limit, _TRAINING_LIST_CAP)
    ordered = sorted(rows, key=lambda item: (item.occurred_at, item.id), reverse=True)
    return tuple(ordered[:bounded])
