"""PostgreSQL repository for append-only experiential memory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Table, desc, func, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from thytrader.memory.models import (
    ActorOrigin,
    DeliveryStatus,
    EvidenceKind,
    ExperientialModel,
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
from thytrader.memory.recording import notes_from_json, notes_to_json
from thytrader.memory.store import MemoryStoreError
from thytrader.memory.trade_reasons import (
    TRADE_REASON_SCHEMA_VERSION,
    TradeReasonNote,
    TradeReasonOrigin,
    TradeReasonRecord,
)
from thytrader.persistence.schema import (
    experiential_journal_entries,
    experiential_models,
    experiential_notifications,
    experiential_pattern_observations,
    experiential_sentiment_snapshots,
    trade_reason_records,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
    from sqlalchemy.sql.selectable import Select


class PostgresExperientialMemoryStore:
    """Persist journals, sentiment, patterns, notifications, models, and why-trade rows."""

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
                reasons = await _count(connection, trade_reason_records)
        except SQLAlchemyError as error:
            raise MemoryStoreError("Experiential memory storage is unavailable.") from error
        return MemoryCounts(
            journals=journals,
            sentiment=sentiment,
            patterns=patterns,
            notifications=notifications,
            trade_reasons=reasons,
        )

    async def append_model(self, model: ExperientialModel) -> ExperientialModel:
        """Insert one trained model or return the existing fingerprint match."""
        existing = await self.get_model_by_fingerprint(model.fingerprint)
        if existing is not None:
            return existing
        values = {
            "id": model.id,
            "recorded_at": model.recorded_at,
            "origin": model.origin.value,
            "engine_id": model.engine_id,
            "seed": model.seed,
            "fingerprint": model.fingerprint,
            "canonical_document": model.model_dump_json(),
        }
        try:
            async with self._engine.begin() as connection:
                await connection.execute(insert(experiential_models).values(**values))
        except IntegrityError:
            raced = await self.get_model_by_fingerprint(model.fingerprint)
            if raced is not None:
                return raced
            raise MemoryStoreError("Experiential memory storage is unavailable.") from None
        except SQLAlchemyError as error:
            raise MemoryStoreError("Experiential memory storage is unavailable.") from error
        return model

    async def get_model(self, model_id: UUID) -> ExperientialModel | None:
        """Load one trained model by id."""
        statement = select(experiential_models).where(experiential_models.c.id == model_id)
        row = await _one(self._engine, statement)
        if row is None:
            return None
        return _model_from_row(row)

    async def get_model_by_fingerprint(self, fingerprint: str) -> ExperientialModel | None:
        """Load one trained model by content identity."""
        statement = select(experiential_models).where(
            experiential_models.c.fingerprint == fingerprint
        )
        row = await _one(self._engine, statement)
        if row is None:
            return None
        return _model_from_row(row)

    async def list_models(self, *, limit: int = 50) -> tuple[ExperientialModel, ...]:
        """Return newest-first trained models."""
        statement = select(experiential_models)
        rows = await _list(
            self._engine,
            statement,
            experiential_models,
            limit,
            timestamp="recorded_at",
        )
        return tuple(_model_from_row(row) for row in rows)

    async def append_trade_reason(self, record: TradeReasonRecord) -> TradeReasonRecord:
        """Insert one why-trade row or return the existing intent-id match."""
        existing = await self.get_trade_reason_by_intent(record.intent_id)
        if existing is not None:
            return existing
        values = _trade_reason_values(record)
        try:
            async with self._engine.begin() as connection:
                await connection.execute(insert(trade_reason_records).values(**values))
        except IntegrityError:
            raced = await self.get_trade_reason_by_intent(record.intent_id)
            if raced is not None:
                return raced
            raise MemoryStoreError("Experiential memory storage is unavailable.") from None
        except SQLAlchemyError as error:
            raise MemoryStoreError("Experiential memory storage is unavailable.") from error
        return record

    async def get_trade_reason_by_intent(self, intent_id: UUID) -> TradeReasonRecord | None:
        """Load one why-trade row by order-intent id."""
        statement = select(trade_reason_records).where(
            trade_reason_records.c.intent_id == intent_id
        )
        row = await _one(self._engine, statement)
        if row is None:
            return None
        return _trade_reason_from_row(row)

    async def list_trade_reasons(
        self,
        *,
        origin: TradeReasonOrigin | None = None,
        deployment_id: UUID | None = None,
        intent_id: UUID | None = None,
        limit: int = 50,
    ) -> tuple[TradeReasonRecord, ...]:
        """Return newest-first why-trade rows."""
        statement = select(trade_reason_records)
        if origin is not None:
            statement = statement.where(trade_reason_records.c.origin == origin.value)
        if deployment_id is not None:
            statement = statement.where(trade_reason_records.c.deployment_id == deployment_id)
        if intent_id is not None:
            statement = statement.where(trade_reason_records.c.intent_id == intent_id)
        rows = await _list(
            self._engine,
            statement,
            trade_reason_records,
            limit,
            timestamp="created_at",
        )
        return tuple(_trade_reason_from_row(row) for row in rows)

    async def append_trade_reason_note(
        self, intent_id: UUID, note: TradeReasonNote
    ) -> TradeReasonRecord:
        """Append one attributed note to an existing why-trade row."""
        existing = await self.get_trade_reason_by_intent(intent_id)
        if existing is None:
            raise ValueError("Trade-reason record was not found.")
        updated = existing.model_copy(update={"notes": (*existing.notes, note)})
        try:
            async with self._engine.begin() as connection:
                await connection.execute(
                    update(trade_reason_records)
                    .where(trade_reason_records.c.intent_id == intent_id)
                    .values(notes_json=notes_to_json(updated.notes))
                )
        except SQLAlchemyError as error:
            raise MemoryStoreError("Experiential memory storage is unavailable.") from error
        return updated


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
    *,
    timestamp: str = "occurred_at",
) -> tuple[RowMapping, ...]:
    """Run a newest-first select."""
    bounded = _bounded_limit(limit)
    stamp = table.c[timestamp]
    ordered = statement.order_by(desc(stamp), desc(table.c.id)).limit(bounded)
    try:
        async with engine.connect() as connection:
            rows = (await connection.execute(ordered)).mappings().all()
    except SQLAlchemyError as error:
        raise MemoryStoreError("Experiential memory storage is unavailable.") from error
    return tuple(rows)


async def _one(
    engine: AsyncEngine,
    statement: Select[tuple[object, ...]],
) -> RowMapping | None:
    """Return one mapping or None."""
    try:
        async with engine.connect() as connection:
            row = (await connection.execute(statement.limit(1))).mappings().first()
    except SQLAlchemyError as error:
        raise MemoryStoreError("Experiential memory storage is unavailable.") from error
    if row is None:
        return None
    return row


async def _count(connection: AsyncConnection, table: Table) -> int:
    """Count rows in one table."""
    result = await connection.execute(select(func.count()).select_from(table))
    return int(result.scalar_one())


def _bounded_limit(limit: int) -> int:
    """Reject non-positive limits and cap list size."""
    if limit < 1:
        raise ValueError("limit must be positive")
    return min(limit, 10_000)


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


def _model_from_row(row: RowMapping) -> ExperientialModel:
    """Revalidate one stored experiential-model document."""
    return ExperientialModel.model_validate_json(str(row["canonical_document"]))


def _trade_reason_values(record: TradeReasonRecord) -> dict[str, object]:
    """Project a why-trade record onto the durable table."""
    strategy = record.strategy
    return {
        "id": record.id,
        "created_at": record.created_at,
        "origin": record.origin.value,
        "intent_id": record.intent_id,
        "deployment_id": record.deployment_id,
        "deployment_kind": record.deployment_kind,
        "mode": record.mode,
        "product_id": record.product_id,
        "purpose": record.purpose,
        "side": record.side,
        "strategy_id": None if strategy is None else strategy.strategy_id,
        "strategy_fingerprint": None if strategy is None else strategy.strategy_fingerprint,
        "strategy_name": None if strategy is None else strategy.name,
        "strategy_version": None if strategy is None else strategy.version,
        "signal_kind": record.signal.kind.value,
        "last_signal": record.signal.last_signal,
        "candle_starts_at": record.signal.candle_starts_at,
        "timeframe": record.signal.timeframe,
        "risk_decision": record.risk.decision,
        "risk_reason_code": record.risk.reason_code,
        "risk_detail": record.risk.detail,
        "policy_fingerprint": record.risk.policy_fingerprint,
        "policy_source": record.risk.policy_source,
        "notes_json": notes_to_json(record.notes),
    }


def _trade_reason_from_row(row: RowMapping) -> TradeReasonRecord:
    """Revalidate one stored why-trade row. Ledger facts are joined on read."""
    strategy = None
    if row["strategy_id"] is not None:
        strategy = {
            "strategy_id": row["strategy_id"],
            "strategy_fingerprint": str(row["strategy_fingerprint"]),
            "name": str(row["strategy_name"]),
            "version": int(row["strategy_version"]),
        }
    return TradeReasonRecord.model_validate(
        {
            "schema_version": TRADE_REASON_SCHEMA_VERSION,
            "id": row["id"],
            "created_at": row["created_at"],
            "origin": str(row["origin"]),
            "intent_id": row["intent_id"],
            "deployment_id": row["deployment_id"],
            "deployment_kind": str(row["deployment_kind"]),
            "mode": str(row["mode"]),
            "product_id": str(row["product_id"]),
            "purpose": str(row["purpose"]),
            "side": str(row["side"]),
            "strategy": strategy,
            "signal": {
                "kind": str(row["signal_kind"]),
                "last_signal": row["last_signal"],
                "candle_starts_at": row["candle_starts_at"],
                "timeframe": row["timeframe"],
            },
            "risk": {
                "decision": str(row["risk_decision"]),
                "reason_code": str(row["risk_reason_code"]),
                "detail": str(row["risk_detail"]),
                "policy_fingerprint": str(row["policy_fingerprint"]),
                "policy_source": str(row["policy_source"]),
            },
            "notes": notes_from_json(str(row["notes_json"])),
        }
    )
