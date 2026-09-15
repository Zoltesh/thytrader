"""PostgreSQL repository for append-only experiential memory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Table, desc, func, insert, select
from sqlalchemy.exc import SQLAlchemyError

from thytrader.memory.models import (
    ActorOrigin,
    DeliveryStatus,
    EvidenceKind,
    JournalEntry,
    JournalKind,
    LessonOutcome,
    MemoryCounts,
    NotificationRecord,
    NotifyProvider,
    NotifySeverity,
    PatternObservation,
    PatternStatus,
    RuntimeMode,
    SentimentLabel,
    SentimentSnapshot,
)
from thytrader.memory.store import MemoryStoreError
from thytrader.persistence.schema import (
    experiential_journal_entries,
    experiential_notifications,
    experiential_pattern_observations,
    experiential_sentiment_snapshots,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
    from sqlalchemy.sql.selectable import Select


class PostgresExperientialMemoryStore:
    """Persist journals, sentiment, patterns, and notifications."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the store to a managed async engine."""
        self._engine = engine

    async def append_journal(self, entry: JournalEntry) -> JournalEntry:
        """Insert one journal row."""
        await _insert(
            self._engine,
            experiential_journal_entries,
            {
                "id": entry.id,
                "occurred_at": entry.occurred_at,
                "recorded_at": entry.recorded_at,
                "origin": entry.origin.value,
                "kind": entry.kind.value,
                "title": entry.title,
                "body": entry.body,
                "evidence_kind": entry.evidence_kind.value,
                "evidence_id": entry.evidence_id,
                "product_id": entry.product_id,
                "runtime_mode": entry.runtime_mode.value,
                "lesson_outcome": entry.lesson_outcome.value,
            },
        )
        return entry

    async def list_journals(
        self,
        *,
        origin: ActorOrigin | None = None,
        kind: JournalKind | None = None,
        limit: int = 50,
    ) -> tuple[JournalEntry, ...]:
        """Return newest-first journals."""
        statement = select(experiential_journal_entries)
        if origin is not None:
            statement = statement.where(experiential_journal_entries.c.origin == origin.value)
        if kind is not None:
            statement = statement.where(experiential_journal_entries.c.kind == kind.value)
        rows = await _list(
            self._engine,
            statement,
            experiential_journal_entries,
            limit,
        )
        return tuple(_journal_from_row(row) for row in rows)

    async def append_sentiment(self, snapshot: SentimentSnapshot) -> SentimentSnapshot:
        """Insert one sentiment snapshot."""
        await _insert(
            self._engine,
            experiential_sentiment_snapshots,
            {
                "id": snapshot.id,
                "occurred_at": snapshot.occurred_at,
                "recorded_at": snapshot.recorded_at,
                "origin": snapshot.origin.value,
                "label": snapshot.label.value,
                "product_id": snapshot.product_id,
                "note": snapshot.note,
                "journal_id": snapshot.journal_id,
            },
        )
        return snapshot

    async def list_sentiment(
        self,
        *,
        origin: ActorOrigin | None = None,
        limit: int = 50,
    ) -> tuple[SentimentSnapshot, ...]:
        """Return newest-first sentiment snapshots."""
        statement = select(experiential_sentiment_snapshots)
        if origin is not None:
            statement = statement.where(experiential_sentiment_snapshots.c.origin == origin.value)
        rows = await _list(
            self._engine,
            statement,
            experiential_sentiment_snapshots,
            limit,
        )
        return tuple(_sentiment_from_row(row) for row in rows)

    async def append_pattern(self, observation: PatternObservation) -> PatternObservation:
        """Insert one pattern observation."""
        await _insert(
            self._engine,
            experiential_pattern_observations,
            {
                "id": observation.id,
                "occurred_at": observation.occurred_at,
                "recorded_at": observation.recorded_at,
                "origin": observation.origin.value,
                "pattern_key": observation.pattern_key,
                "name": observation.name,
                "hypothesis": observation.hypothesis,
                "status": observation.status.value,
                "evidence_kind": observation.evidence_kind.value,
                "evidence_id": observation.evidence_id,
                "note": observation.note,
            },
        )
        return observation

    async def list_patterns(
        self,
        *,
        origin: ActorOrigin | None = None,
        pattern_key: str | None = None,
        limit: int = 50,
    ) -> tuple[PatternObservation, ...]:
        """Return newest-first pattern observations."""
        statement = select(experiential_pattern_observations)
        if origin is not None:
            statement = statement.where(experiential_pattern_observations.c.origin == origin.value)
        if pattern_key is not None:
            statement = statement.where(
                experiential_pattern_observations.c.pattern_key == pattern_key
            )
        rows = await _list(
            self._engine,
            statement,
            experiential_pattern_observations,
            limit,
        )
        return tuple(_pattern_from_row(row) for row in rows)

    async def append_notification(self, record: NotificationRecord) -> NotificationRecord:
        """Insert one notification attempt."""
        await _insert(
            self._engine,
            experiential_notifications,
            {
                "id": record.id,
                "occurred_at": record.occurred_at,
                "recorded_at": record.recorded_at,
                "origin": record.origin.value,
                "title": record.title,
                "body": record.body,
                "severity": record.severity.value,
                "provider": record.provider.value,
                "delivery_status": record.delivery_status.value,
                "detail": record.detail,
                "journal_id": record.journal_id,
            },
        )
        return record

    async def list_notifications(
        self,
        *,
        origin: ActorOrigin | None = None,
        limit: int = 50,
    ) -> tuple[NotificationRecord, ...]:
        """Return newest-first notification attempts."""
        statement = select(experiential_notifications)
        if origin is not None:
            statement = statement.where(experiential_notifications.c.origin == origin.value)
        rows = await _list(self._engine, statement, experiential_notifications, limit)
        return tuple(_notification_from_row(row) for row in rows)

    async def counts(self) -> MemoryCounts:
        """Return table counts."""
        try:
            async with self._engine.connect() as connection:
                journals = await _count(connection, experiential_journal_entries)
                sentiment = await _count(connection, experiential_sentiment_snapshots)
                patterns = await _count(connection, experiential_pattern_observations)
                notifications = await _count(connection, experiential_notifications)
        except SQLAlchemyError as error:
            raise MemoryStoreError("Experiential memory storage is unavailable.") from error
        return MemoryCounts(
            journals=journals,
            sentiment=sentiment,
            patterns=patterns,
            notifications=notifications,
        )


async def _insert(engine: AsyncEngine, table: Table, values: dict[str, object]) -> None:
    """Insert one row or raise MemoryStoreError."""
    try:
        async with engine.begin() as connection:
            await connection.execute(insert(table).values(**values))
    except SQLAlchemyError as error:
        raise MemoryStoreError("Experiential memory storage is unavailable.") from error


async def _list(
    engine: AsyncEngine,
    statement: Select[tuple[object, ...]],
    table: Table,
    limit: int,
) -> tuple[RowMapping, ...]:
    """Run a newest-first select."""
    bounded = _bounded_limit(limit)
    ordered = statement.order_by(desc(table.c.occurred_at), desc(table.c.id)).limit(bounded)
    try:
        async with engine.connect() as connection:
            rows = (await connection.execute(ordered)).mappings().all()
    except SQLAlchemyError as error:
        raise MemoryStoreError("Experiential memory storage is unavailable.") from error
    return tuple(rows)


async def _count(connection: AsyncConnection, table: Table) -> int:
    """Count rows in one table."""
    result = await connection.execute(select(func.count()).select_from(table))
    return int(result.scalar_one())


def _bounded_limit(limit: int) -> int:
    """Reject non-positive limits and cap list size."""
    if limit < 1:
        raise ValueError("limit must be positive")
    return min(limit, 100)


def _journal_from_row(row: RowMapping) -> JournalEntry:
    """Revalidate one stored journal row."""
    return JournalEntry(
        id=row["id"],
        occurred_at=row["occurred_at"],
        recorded_at=row["recorded_at"],
        origin=ActorOrigin(str(row["origin"])),
        kind=JournalKind(str(row["kind"])),
        title=str(row["title"]),
        body=str(row["body"]),
        evidence_kind=EvidenceKind(str(row["evidence_kind"])),
        evidence_id=row["evidence_id"],
        product_id=row["product_id"],
        runtime_mode=RuntimeMode(str(row["runtime_mode"])),
        lesson_outcome=LessonOutcome(str(row["lesson_outcome"])),
    )


def _sentiment_from_row(row: RowMapping) -> SentimentSnapshot:
    """Revalidate one stored sentiment row."""
    return SentimentSnapshot(
        id=row["id"],
        occurred_at=row["occurred_at"],
        recorded_at=row["recorded_at"],
        origin=ActorOrigin(str(row["origin"])),
        label=SentimentLabel(str(row["label"])),
        product_id=row["product_id"],
        note=str(row["note"]),
        journal_id=row["journal_id"],
    )


def _pattern_from_row(row: RowMapping) -> PatternObservation:
    """Revalidate one stored pattern row."""
    return PatternObservation(
        id=row["id"],
        occurred_at=row["occurred_at"],
        recorded_at=row["recorded_at"],
        origin=ActorOrigin(str(row["origin"])),
        pattern_key=str(row["pattern_key"]),
        name=str(row["name"]),
        hypothesis=str(row["hypothesis"]),
        status=PatternStatus(str(row["status"])),
        evidence_kind=EvidenceKind(str(row["evidence_kind"])),
        evidence_id=row["evidence_id"],
        note=str(row["note"]),
    )


def _notification_from_row(row: RowMapping) -> NotificationRecord:
    """Revalidate one stored notification row."""
    return NotificationRecord(
        id=row["id"],
        occurred_at=row["occurred_at"],
        recorded_at=row["recorded_at"],
        origin=ActorOrigin(str(row["origin"])),
        title=str(row["title"]),
        body=str(row["body"]),
        severity=NotifySeverity(str(row["severity"])),
        provider=NotifyProvider(str(row["provider"])),
        delivery_status=DeliveryStatus(str(row["delivery_status"])),
        detail=str(row["detail"]),
        journal_id=row["journal_id"],
    )
