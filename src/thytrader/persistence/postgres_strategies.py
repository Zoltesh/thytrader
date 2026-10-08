"""PostgreSQL repository for mutable strategies, content-addressed snapshots, and deletion.

Rows in ``strategies`` are the root object (ADR 0082). Snapshots are verified on
every load: stored bytes must be canonical, hash to their fingerprint, and name the
owning strategy. Deletion runs in one transaction that locks the strategy row, so
no snapshot (backtest/study/deploy start) can race it. Snapshot rows and dataset checks
live in :mod:`thytrader.persistence.postgres_strategy_snapshots` and the deletion steps in
:mod:`thytrader.persistence.postgres_strategy_deletion`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import (
    ColumnElement,
    Select,
    cast as sql_cast,
    delete,
    func,
    literal_column,
    not_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.persistence.postgres_portfolio_rows import (
    remove_strategy_sleeves_in,
)
from thytrader.persistence.postgres_strategy_deletion import (
    _blocking_deployments,
    _delete_research,
    _delete_unreferenced_snapshots,
    _deletion_counts,
    _journal_instant,
    _remove_allocation,
    _strategy_row,
)
from thytrader.persistence.postgres_strategy_snapshots import (
    _FINGERPRINT_PATTERN,
    _snapshot_from_row,
    _store_snapshot,
    _validate_fingerprint,
    _verify_compatible_dataset,
)
from thytrader.persistence.schema import (
    strategies,
    strategy_dataset_bindings,
    strategy_snapshots,
)
from thytrader.risk.store import RiskPolicyStoreError
from thytrader.strategies.library import (
    RESEARCH_TAG,
    RESEARCH_TAG_PREFIX,
    SnapshotLookup,
    StrategyDeletionBlockedError,
    StrategyDeletionPreview,
    StrategyDeletionResult,
    StrategyDocument,
    StrategyInvalidError,
    StrategyLibraryError,
    StrategyNotFoundError,
    StrategyOrigin,
    StrategyOriginCounts,
    StrategyPage,
    StrategyRecord,
    StrategyRevisionConflictError,
    StrategySnapshotNotFoundError,
    StrategyStorageUnavailableError,
    StrategyValidation,
    authoring_issues,
    evaluate_document,
    parse_document_text,
    validation_from_json,
    validation_issues_json,
)
from thytrader.strategies.models import (
    StrategyDefinition,
    canonical_strategy_bytes,
    strategy_fingerprint,
)
from thytrader.strategies.snapshots import (
    StrategyDatasetBinding,
    StrategySnapshot,
    StrategySnapshotError,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from thytrader.market_data.datasets import DatasetStore

_UNAVAILABLE = "Strategy storage is unavailable."
_SNAPSHOT_UNAVAILABLE = "Strategy snapshot storage is unavailable."
_BINDING_UNAVAILABLE = "Strategy dataset binding storage is unavailable."


class PostgresStrategyStore:
    """Persist mutable strategies and verify immutable snapshots in PostgreSQL."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Use one application-managed asynchronous database engine."""
        self._engine = engine

    async def create(
        self, document: StrategyDocument, *, strategy_id: UUID, created_at: datetime
    ) -> StrategyRecord:
        """Insert one new strategy at revision 1, storing its validation result."""
        _require_utc(created_at)
        evaluated = evaluate_document(document, strategy_id=strategy_id, created_at=created_at)
        statement = (
            insert(strategies)
            .values(
                strategy_id=str(strategy_id),
                name=evaluated.name,
                product_id=evaluated.product_id,
                timeframe=evaluated.timeframe,
                document=evaluated.stored_text,
                is_valid=evaluated.validation.valid,
                validation_issues=validation_issues_json(evaluated.validation),
                current_fingerprint=evaluated.fingerprint,
                revision=1,
                created_at=created_at,
                updated_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing()
            .returning(strategies.c.strategy_id)
        )
        try:
            async with self._engine.begin() as connection:
                inserted = (await connection.execute(statement)).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise StrategyStorageUnavailableError(_UNAVAILABLE) from error
        if inserted is None:
            raise StrategyLibraryError("A strategy with this identity already exists.")
        return await self.get(strategy_id)

    async def get(self, strategy_id: UUID) -> StrategyRecord:
        """Load and verify one strategy row."""
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(_strategy_select(strategy_id))
                row = result.mappings().one_or_none()
        except SQLAlchemyError as error:
            raise StrategyStorageUnavailableError(_UNAVAILABLE) from error
        if row is None:
            raise StrategyNotFoundError("Strategy was not found.")
        return _record_from_row(row)

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        tag: str | None = None,
        origin: StrategyOrigin = StrategyOrigin.ALL,
    ) -> StrategyPage:
        """Return one newest-updated-first page and the total library size.

        ``tag`` filters on the stored document's ``metadata.tags`` (JSONB containment);
        ``origin`` keeps research (``claude-research`` / ``research-*``) or operator
        strategies (ADR 0098). ``total`` then counts only matching strategies.
        """
        statement = (
            _strategy_select(None)
            .order_by(strategies.c.updated_at.desc(), strategies.c.strategy_id.asc())
            .limit(limit)
            .offset(offset)
        )
        research = _research_tagged()
        counted = select(
            func.count().label("all"), func.count().filter(research).label("research")
        ).select_from(strategies)
        if tag is not None:
            tagged = sql_cast(strategies.c.document, JSONB)["metadata"]["tags"].contains([tag])
            statement = statement.where(tagged)
            counted = counted.where(tagged)
        if origin is not StrategyOrigin.ALL:
            scoped = research if origin is StrategyOrigin.RESEARCH else not_(research)
            statement = statement.where(scoped)
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
                counts = (await connection.execute(counted)).mappings().one()
        except SQLAlchemyError as error:
            raise StrategyStorageUnavailableError(_UNAVAILABLE) from error
        origins = StrategyOriginCounts(
            operator=int(counts["all"]) - int(counts["research"]),
            research=int(counts["research"]),
            all=int(counts["all"]),
        )
        total = (
            origins.all
            if origin is StrategyOrigin.ALL
            else origins.research
            if origin is StrategyOrigin.RESEARCH
            else origins.operator
        )
        return StrategyPage(
            records=tuple(_record_from_row(row) for row in rows),
            total=total,
            origin_counts=origins,
        )

    async def save(
        self, strategy_id: UUID, document: StrategyDocument, *, expected_revision: int
    ) -> StrategyRecord:
        """Replace the document only when ``expected_revision`` is still current."""
        try:
            async with self._engine.begin() as connection:
                await _save_locked(
                    connection, strategy_id, document, expected_revision=expected_revision
                )
        except SQLAlchemyError as error:
            raise StrategyStorageUnavailableError(_UNAVAILABLE) from error
        return await self.get(strategy_id)

    async def snapshot(self, strategy_id: UUID) -> StrategySnapshot:
        """Snapshot the current valid definition; identical content reuses one row."""
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(
                    _strategy_select(strategy_id).with_for_update(read=True)
                )
                row = result.mappings().one_or_none()
                if row is None:
                    raise StrategyNotFoundError("Strategy was not found.")
                record = _record_from_row(row)
                if record.definition is None:
                    raise StrategyInvalidError(record.validation.issues)
                return await _store_snapshot(connection, record.definition)
        except SQLAlchemyError as error:
            raise StrategyStorageUnavailableError(_UNAVAILABLE) from error

    async def record_snapshot(self, definition: StrategyDefinition) -> StrategySnapshot:
        """Store one derived (sweep) definition owned by an existing strategy."""
        try:
            async with self._engine.begin() as connection:
                owner = (
                    await connection.execute(
                        select(strategies.c.strategy_id)
                        .where(strategies.c.strategy_id == str(definition.strategy_id))
                        .with_for_update(read=True)
                    )
                ).scalar_one_or_none()
                if owner is None:
                    raise StrategySnapshotError("Owning strategy was not found.")
                return await _store_snapshot(connection, definition)
        except SQLAlchemyError as error:
            raise StrategySnapshotError(_SNAPSHOT_UNAVAILABLE) from error

    async def load(self, strategy_fingerprint_value: str) -> StrategySnapshot:
        """Load and cryptographically verify one exact snapshot."""
        _validate_fingerprint(strategy_fingerprint_value, label="strategy")
        statement = select(
            strategy_snapshots.c.strategy_id,
            strategy_snapshots.c.canonical_definition,
        ).where(strategy_snapshots.c.strategy_fingerprint == strategy_fingerprint_value)
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise StrategySnapshotError(_SNAPSHOT_UNAVAILABLE) from error
        if row is None:
            raise StrategySnapshotError("Strategy snapshot was not found.")
        return _snapshot_from_row(row, strategy_fingerprint_value)

    async def lookup_snapshot(self, strategy_fingerprint_value: str) -> SnapshotLookup:
        """Load one snapshot with its owner so old links can resolve the strategy."""
        if _FINGERPRINT_PATTERN.fullmatch(strategy_fingerprint_value) is None:
            raise StrategySnapshotNotFoundError("Strategy snapshot was not found.")
        statement = (
            select(
                strategy_snapshots.c.strategy_id,
                strategy_snapshots.c.canonical_definition,
                strategy_snapshots.c.created_at,
                strategies.c.name,
                strategies.c.current_fingerprint,
            )
            .select_from(
                strategy_snapshots.outerjoin(
                    strategies, strategies.c.strategy_id == strategy_snapshots.c.strategy_id
                )
            )
            .where(strategy_snapshots.c.strategy_fingerprint == strategy_fingerprint_value)
        )
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise StrategyStorageUnavailableError(_UNAVAILABLE) from error
        if row is None:
            raise StrategySnapshotNotFoundError("Strategy snapshot was not found.")
        try:
            snapshot = _snapshot_from_row(row, strategy_fingerprint_value)
        except StrategySnapshotError as error:
            raise StrategyStorageUnavailableError(str(error)) from error
        owner = cast("str | None", row["strategy_id"])
        return SnapshotLookup(
            snapshot=snapshot,
            strategy_id=None if owner is None else UUID(owner),
            strategy_name=cast("str | None", row["name"]),
            created_at=cast("datetime", row["created_at"]),
            is_current=row["current_fingerprint"] == strategy_fingerprint_value,
        )

    async def preview_deletion(self, strategy_id: UUID) -> StrategyDeletionPreview:
        """Count what deletion would remove, and which bots block it."""
        try:
            async with self._engine.connect() as connection:
                target = await _strategy_row(connection, strategy_id, lock=False)
                counts = await _deletion_counts(connection, target.strategy_id)
                blocking = await _blocking_deployments(connection, target.strategy_id)
        except (SQLAlchemyError, RiskPolicyStoreError) as error:
            raise StrategyStorageUnavailableError(_UNAVAILABLE) from error
        return StrategyDeletionPreview(
            strategy_id=strategy_id,
            name=target.name,
            counts=counts,
            blocking_deployment_ids=blocking,
        )

    async def delete(self, strategy_id: UUID) -> StrategyDeletionResult:
        """Delete a strategy atomically, retaining all stopped execution risk evidence."""
        try:
            async with self._engine.begin() as connection:
                target = await _strategy_row(connection, strategy_id, lock=True)
                blocking = await _blocking_deployments(connection, target.strategy_id, lock=True)
                if blocking:
                    raise StrategyDeletionBlockedError(blocking)
                counts = await _deletion_counts(connection, target.strategy_id)
                await remove_strategy_sleeves_in(
                    connection, target.strategy_id, occurred_at=_journal_instant()
                )
                await _delete_research(connection, target.strategy_id)
                await _delete_unreferenced_snapshots(connection, target.strategy_id)
                await connection.execute(
                    delete(strategies).where(strategies.c.strategy_id == target.strategy_id)
                )
                republished = await _remove_allocation(connection, strategy_id)
        except (SQLAlchemyError, RiskPolicyStoreError) as error:
            raise StrategyStorageUnavailableError(_UNAVAILABLE) from error
        return StrategyDeletionResult(
            strategy_id=strategy_id,
            name=target.name,
            counts=counts,
            risk_policy_republished=republished,
        )

    async def bind_dataset(
        self,
        strategy_fingerprint_value: str,
        dataset_fingerprint: str,
        *,
        dataset_store: DatasetStore,
        bound_at: datetime,
    ) -> StrategyDatasetBinding:
        """Idempotently bind exact verified snapshot and dataset identities."""
        _require_utc(bound_at)
        snapshot = await self.load(strategy_fingerprint_value)
        _validate_fingerprint(dataset_fingerprint, label="dataset")
        _verify_compatible_dataset(snapshot, dataset_fingerprint, dataset_store)
        statement = (
            insert(strategy_dataset_bindings)
            .values(
                strategy_fingerprint=strategy_fingerprint_value,
                dataset_fingerprint=dataset_fingerprint,
                strategy_id=str(snapshot.definition.strategy_id),
                bound_at=bound_at,
            )
            .on_conflict_do_nothing(index_elements=["strategy_fingerprint", "dataset_fingerprint"])
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise StrategySnapshotError(_BINDING_UNAVAILABLE) from error
        return await self.load_binding(
            strategy_fingerprint_value,
            dataset_fingerprint,
            dataset_store=dataset_store,
        )

    async def load_binding(
        self,
        strategy_fingerprint_value: str,
        dataset_fingerprint: str,
        *,
        dataset_store: DatasetStore,
    ) -> StrategyDatasetBinding:
        """Load one association after re-verifying both immutable artifacts."""
        _validate_fingerprint(strategy_fingerprint_value, label="strategy")
        _validate_fingerprint(dataset_fingerprint, label="dataset")
        snapshot = await self.load(strategy_fingerprint_value)
        _verify_compatible_dataset(snapshot, dataset_fingerprint, dataset_store)
        statement = select(strategy_dataset_bindings.c.bound_at).where(
            strategy_dataset_bindings.c.strategy_fingerprint == strategy_fingerprint_value,
            strategy_dataset_bindings.c.dataset_fingerprint == dataset_fingerprint,
        )
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise StrategySnapshotError(_BINDING_UNAVAILABLE) from error
        if row is None:
            raise StrategySnapshotError("Strategy dataset binding was not found.")
        return StrategyDatasetBinding(
            strategy_fingerprint=strategy_fingerprint_value,
            dataset_fingerprint=dataset_fingerprint,
            bound_at=cast("datetime", row["bound_at"]),
        )


async def _save_locked(
    connection: AsyncConnection,
    strategy_id: UUID,
    document: StrategyDocument,
    *,
    expected_revision: int,
) -> None:
    """Row-lock, compare revisions, and replace one strategy document."""
    result = await connection.execute(
        select(strategies.c.name, strategies.c.revision, strategies.c.created_at)
        .where(strategies.c.strategy_id == str(strategy_id))
        .with_for_update()
    )
    current = result.mappings().one_or_none()
    if current is None:
        raise StrategyNotFoundError("Strategy was not found.")
    revision = int(cast("int", current["revision"]))
    if revision != expected_revision:
        raise StrategyRevisionConflictError(revision)
    evaluated = evaluate_document(
        document,
        strategy_id=strategy_id,
        created_at=cast("datetime", current["created_at"]),
        fallback_name=cast("str", current["name"]),
    )
    await connection.execute(
        update(strategies)
        .where(
            strategies.c.strategy_id == str(strategy_id),
            strategies.c.revision == expected_revision,
        )
        .values(
            name=evaluated.name,
            product_id=evaluated.product_id,
            timeframe=evaluated.timeframe,
            document=evaluated.stored_text,
            is_valid=evaluated.validation.valid,
            validation_issues=validation_issues_json(evaluated.validation),
            current_fingerprint=evaluated.fingerprint,
            revision=expected_revision + 1,
            updated_at=datetime.now(UTC),
        )
    )


def _strategy_select(strategy_id: UUID | None) -> Select[tuple[object, ...]]:
    """Select every strategy column, optionally for one identity."""
    statement: Select[tuple[object, ...]] = select(
        strategies.c.strategy_id,
        strategies.c.name,
        strategies.c.product_id,
        strategies.c.timeframe,
        strategies.c.document,
        strategies.c.is_valid,
        strategies.c.validation_issues,
        strategies.c.current_fingerprint,
        strategies.c.revision,
        strategies.c.created_at,
        strategies.c.updated_at,
    )
    if strategy_id is not None:
        statement = statement.where(strategies.c.strategy_id == str(strategy_id))
    return statement


def _record_from_row(row: RowMapping) -> StrategyRecord:
    """Rebuild and verify one strategy record from its stored columns."""
    try:
        document = parse_document_text(cast("str", row["document"]))
        validation = validation_from_json(cast("str", row["validation_issues"]))
    except (StrategyLibraryError, TypeError, ValueError) as error:
        raise StrategyStorageUnavailableError("Stored strategy row is malformed.") from error
    definition: StrategyDefinition | None = None
    fingerprint = cast("str | None", row["current_fingerprint"])
    if bool(row["is_valid"]):
        definition = _verified_definition(cast("str", row["document"]), fingerprint)
        validation = StrategyValidation()
        retired = authoring_issues(definition)
        if retired:
            # Saved before the value was retired: still verifies, but cannot run again.
            definition = None
            fingerprint = None
            validation = StrategyValidation(issues=retired)
    revision = row["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise StrategyStorageUnavailableError("Stored strategy revision is invalid.")
    return StrategyRecord(
        strategy_id=UUID(cast("str", row["strategy_id"])),
        name=cast("str", row["name"]),
        revision=revision,
        created_at=cast("datetime", row["created_at"]),
        updated_at=cast("datetime", row["updated_at"]),
        document=document,
        definition=definition,
        validation=validation,
        current_fingerprint=fingerprint,
        product_id=cast("str | None", row["product_id"]),
        timeframe=cast("str | None", row["timeframe"]),
    )


def _verified_definition(canonical: str, fingerprint: str | None) -> StrategyDefinition:
    """Require a valid row's document to be canonical and match its stored fingerprint."""
    try:
        definition = StrategyDefinition.model_validate_json(canonical)
    except ValidationError as error:
        raise StrategyStorageUnavailableError("Stored strategy failed validation.") from error
    if canonical_strategy_bytes(definition).decode("utf-8") != canonical:
        raise StrategyStorageUnavailableError("Stored strategy bytes are not canonical.")
    if strategy_fingerprint(definition) != fingerprint:
        raise StrategyStorageUnavailableError("Stored strategy fingerprint does not match.")
    return definition


def _require_utc(value: datetime) -> None:
    """Require timezone-aware UTC timestamps."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise StrategyLibraryError("Strategy timestamps must be UTC.")


_RESEARCH_TAG_PATH = (
    f'$.metadata.tags[*] ? (@ == "{RESEARCH_TAG}" || @ starts with "{RESEARCH_TAG_PREFIX}")'
)
"""SQL/JSON path matching any research tag; built from module constants, never input."""


def _research_tagged() -> ColumnElement[bool]:
    """True when the stored document carries ``claude-research`` or a ``research-*`` tag.

    Lax-mode JSON path: a missing or malformed ``metadata.tags`` matches nothing, and the
    coalesce keeps the negation (operator strategies) total.
    """
    path = literal_column(f"'{_RESEARCH_TAG_PATH}'::jsonpath")
    exists = func.jsonb_path_exists(sql_cast(strategies.c.document, JSONB), path)
    return func.coalesce(exists, False)
