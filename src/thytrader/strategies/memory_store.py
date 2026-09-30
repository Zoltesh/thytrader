"""Process-local strategy and snapshot store for tests and database-less API processes.

Semantics match :class:`~thytrader.persistence.postgres_strategies.PostgresStrategyStore`
for strategy rows and snapshots (revision guard, validation on save, deduplicated
content-addressed snapshots). Deletion here only knows about strategies and
snapshots; research evidence and deployment ledgers live in PostgreSQL, so an
optional callback reports blocking (running/paused) deployments.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader.strategies.library import (
    DEFAULT_STRATEGY_NAME,
    SnapshotLookup,
    StrategyDeletionBlockedError,
    StrategyDeletionCounts,
    StrategyDeletionPreview,
    StrategyDeletionResult,
    StrategyDocument,
    StrategyInvalidError,
    StrategyLibraryError,
    StrategyNotFoundError,
    StrategyPage,
    StrategyRecord,
    StrategyRevisionConflictError,
    StrategySnapshotNotFoundError,
    evaluate_document,
    parse_document_text,
)
from thytrader.strategies.models import canonical_strategy_bytes, strategy_fingerprint
from thytrader.strategies.snapshots import StrategySnapshot, StrategySnapshotError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from thytrader.strategies.models import StrategyDefinition


@dataclass(slots=True)
class InMemoryStrategyStore:
    """Keep strategies and snapshots in memory with the durable store's rules."""

    blocking_deployments: Callable[[UUID], Awaitable[tuple[UUID, ...]]] | None = None
    _records: dict[UUID, StrategyRecord] = field(default_factory=dict)
    _snapshots: dict[str, tuple[StrategySnapshot, UUID | None, datetime]] = field(
        default_factory=dict
    )
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def create(
        self, document: StrategyDocument, *, strategy_id: UUID, created_at: datetime
    ) -> StrategyRecord:
        """Insert one new strategy at revision 1."""
        async with self._lock:
            if strategy_id in self._records:
                raise StrategyLibraryError("A strategy with this identity already exists.")
            record = _record(document, strategy_id, created_at, revision=1, fallback=None)
            self._records[strategy_id] = record
            return record

    async def get(self, strategy_id: UUID) -> StrategyRecord:
        """Load one strategy."""
        async with self._lock:
            return self._require(strategy_id)

    async def list_page(self, *, limit: int, offset: int) -> StrategyPage:
        """Return one newest-updated-first page."""
        async with self._lock:
            ordered = sorted(
                self._records.values(),
                key=lambda item: (-item.updated_at.timestamp(), str(item.strategy_id)),
            )
            return StrategyPage(records=tuple(ordered[offset : offset + limit]), total=len(ordered))

    async def save(
        self, strategy_id: UUID, document: StrategyDocument, *, expected_revision: int
    ) -> StrategyRecord:
        """Replace the document only when ``expected_revision`` is current."""
        async with self._lock:
            current = self._require(strategy_id)
            if current.revision != expected_revision:
                raise StrategyRevisionConflictError(current.revision)
            record = _record(
                document,
                strategy_id,
                current.created_at,
                revision=current.revision + 1,
                fallback=current.name,
            )
            self._records[strategy_id] = record
            return record

    async def snapshot(self, strategy_id: UUID) -> StrategySnapshot:
        """Snapshot the current valid definition (deduplicated by fingerprint)."""
        async with self._lock:
            record = self._require(strategy_id)
            if record.definition is None:
                raise StrategyInvalidError(record.validation.issues)
            return self._store(record.definition)

    async def record_snapshot(self, definition: StrategyDefinition) -> StrategySnapshot:
        """Store one derived definition owned by an existing strategy."""
        async with self._lock:
            if definition.strategy_id not in self._records:
                raise StrategySnapshotError("Owning strategy was not found.")
            return self._store(definition)

    async def load(self, strategy_fingerprint_value: str) -> StrategySnapshot:
        """Load one snapshot by fingerprint."""
        async with self._lock:
            stored = self._snapshots.get(strategy_fingerprint_value)
        if stored is None:
            raise StrategySnapshotError("Strategy snapshot was not found.")
        return stored[0]

    async def lookup_snapshot(self, strategy_fingerprint_value: str) -> SnapshotLookup:
        """Load one snapshot with its owner."""
        async with self._lock:
            stored = self._snapshots.get(strategy_fingerprint_value)
            if stored is None:
                raise StrategySnapshotNotFoundError("Strategy snapshot was not found.")
            snapshot, owner, created_at = stored
            record = None if owner is None else self._records.get(owner)
        return SnapshotLookup(
            snapshot=snapshot,
            strategy_id=owner,
            strategy_name=None if record is None else record.name,
            created_at=created_at,
            is_current=record is not None
            and record.current_fingerprint == strategy_fingerprint_value,
        )

    async def preview_deletion(self, strategy_id: UUID) -> StrategyDeletionPreview:
        """Count the snapshots a deletion would remove and report blocking bots."""
        record = await self.get(strategy_id)
        blocking = await self._blocking(strategy_id)
        return StrategyDeletionPreview(
            strategy_id=strategy_id,
            name=record.name,
            counts=StrategyDeletionCounts(snapshots=self._owned_snapshots(strategy_id)),
            blocking_deployment_ids=blocking,
        )

    async def delete(self, strategy_id: UUID) -> StrategyDeletionResult:
        """Delete one strategy and its snapshots unless a bot is running or paused."""
        blocking = await self._blocking(strategy_id)
        if blocking:
            raise StrategyDeletionBlockedError(blocking)
        async with self._lock:
            record = self._require(strategy_id)
            counts = StrategyDeletionCounts(snapshots=self._owned_snapshots(strategy_id))
            del self._records[strategy_id]
            for fingerprint in [
                key for key, value in self._snapshots.items() if value[1] == strategy_id
            ]:
                del self._snapshots[fingerprint]
        return StrategyDeletionResult(
            strategy_id=strategy_id,
            name=record.name,
            counts=counts,
            risk_policy_republished=False,
        )

    def seed_definition(self, definition: StrategyDefinition) -> StrategySnapshot:
        """Synchronously install one valid definition as a strategy and snapshot it.

        Test and fixture helper: equivalent to ``create`` followed by ``snapshot``.
        """
        document = parse_document_text(canonical_strategy_bytes(definition).decode("utf-8"))
        self._records[definition.strategy_id] = _record(
            document, definition.strategy_id, definition.created_at, revision=1, fallback=None
        )
        return self._store(definition)

    def snapshot_fingerprints(self) -> tuple[str, ...]:
        """Return every stored snapshot fingerprint (test and diagnostics helper)."""
        return tuple(self._snapshots)

    def _require(self, strategy_id: UUID) -> StrategyRecord:
        """Return one record or raise not-found."""
        record = self._records.get(strategy_id)
        if record is None:
            raise StrategyNotFoundError("Strategy was not found.")
        return record

    def _store(self, definition: StrategyDefinition) -> StrategySnapshot:
        """Insert or reuse one snapshot."""
        fingerprint = strategy_fingerprint(definition)
        existing = self._snapshots.get(fingerprint)
        if existing is not None:
            return existing[0]
        snapshot = StrategySnapshot(strategy_fingerprint=fingerprint, definition=definition)
        self._snapshots[fingerprint] = (snapshot, definition.strategy_id, datetime.now(UTC))
        return snapshot

    def _owned_snapshots(self, strategy_id: UUID) -> int:
        """Count snapshots owned by one strategy."""
        return sum(1 for value in self._snapshots.values() if value[1] == strategy_id)

    async def _blocking(self, strategy_id: UUID) -> tuple[UUID, ...]:
        """Ask the optional callback which deployments block deletion."""
        if self.blocking_deployments is None:
            return ()
        return await self.blocking_deployments(strategy_id)


def _record(
    document: StrategyDocument,
    strategy_id: UUID,
    created_at: datetime,
    *,
    revision: int,
    fallback: str | None,
) -> StrategyRecord:
    """Evaluate one document into a stored record."""
    evaluated = evaluate_document(
        document,
        strategy_id=strategy_id,
        created_at=created_at,
        fallback_name=fallback or DEFAULT_STRATEGY_NAME,
    )
    return StrategyRecord(
        strategy_id=strategy_id,
        name=evaluated.name,
        revision=revision,
        created_at=created_at,
        updated_at=datetime.now(UTC),
        document=evaluated.document,
        definition=evaluated.definition,
        validation=evaluated.validation,
        current_fingerprint=evaluated.fingerprint,
        product_id=evaluated.product_id,
        timeframe=evaluated.timeframe,
    )
