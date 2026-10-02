"""PostgreSQL repository for mutable strategies, content-addressed snapshots, and deletion.

Rows in ``strategies`` are the root object (ADR 0082). Snapshots are verified on
every load: stored bytes must be canonical, hash to their fingerprint, and name the
owning strategy. Deletion runs in one transaction that locks the strategy row, so
no snapshot (backtest/study/deploy start) can race it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re
from typing import TYPE_CHECKING, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import (
    ColumnElement,
    ScalarSelect,
    Select,
    Table,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.market_data.datasets import DatasetStoreError
from thytrader.persistence.postgres_portfolio_rows import (
    count_strategy_sleeves,
    remove_strategy_sleeves_in,
)
from thytrader.persistence.postgres_risk import load_active_policy_in, publish_policy_in
from thytrader.persistence.schema import (
    deployments,
    execution_fills,
    execution_instrument_state,
    execution_orders,
    execution_positions,
    order_intents,
    published_backtest_results,
    published_research_run_specs,
    published_research_studies,
    research_jobs,
    research_study_strategies,
    strategies,
    strategy_dataset_bindings,
    strategy_snapshots,
    trade_reason_records,
)
from thytrader.risk.store import RiskPolicyStoreError, successor_without_allocation
from thytrader.strategies.library import (
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
    covered_product_ids,
    expanded_data_requirements,
    strategy_fingerprint,
)
from thytrader.strategies.snapshots import (
    StrategyDatasetBinding,
    StrategyDatasetMismatchError,
    StrategySnapshot,
    StrategySnapshotError,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from thytrader.market_data.datasets import DatasetStore

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_ACTIVE_STATUSES = ("running", "paused")
_UNAVAILABLE = "Strategy storage is unavailable."
_SNAPSHOT_UNAVAILABLE = "Strategy snapshot storage is unavailable."
_BINDING_UNAVAILABLE = "Strategy dataset binding storage is unavailable."


@dataclass(frozen=True, slots=True)
class _LockedStrategy:
    """The strategy row fields that deletion needs."""

    strategy_id: str
    name: str


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

    async def list_page(self, *, limit: int, offset: int) -> StrategyPage:
        """Return one newest-updated-first page and the total library size."""
        statement = (
            _strategy_select(None)
            .order_by(strategies.c.updated_at.desc(), strategies.c.strategy_id.asc())
            .limit(limit)
            .offset(offset)
        )
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
                total = (
                    await connection.execute(select(func.count()).select_from(strategies))
                ).scalar_one()
        except SQLAlchemyError as error:
            raise StrategyStorageUnavailableError(_UNAVAILABLE) from error
        return StrategyPage(records=tuple(_record_from_row(row) for row in rows), total=int(total))

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
        """Hard-delete one strategy in one transaction, keeping stopped live books."""
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
                await _delete_paper_books(connection, target.strategy_id)
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


async def _store_snapshot(
    connection: AsyncConnection, definition: StrategyDefinition
) -> StrategySnapshot:
    """Insert (or reuse) one snapshot row and verify the stored bytes."""
    canonical = canonical_strategy_bytes(definition).decode("utf-8")
    fingerprint = strategy_fingerprint(definition)
    await connection.execute(
        insert(strategy_snapshots)
        .values(
            strategy_fingerprint=fingerprint,
            strategy_id=str(definition.strategy_id),
            canonical_definition=canonical,
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["strategy_fingerprint"])
    )
    result = await connection.execute(
        select(
            strategy_snapshots.c.strategy_id,
            strategy_snapshots.c.canonical_definition,
        ).where(strategy_snapshots.c.strategy_fingerprint == fingerprint)
    )
    return _snapshot_from_row(result.mappings().one(), fingerprint)


def _snapshot_from_row(row: RowMapping, strategy_fingerprint_value: str) -> StrategySnapshot:
    """Validate one snapshot row against its canonical identity and owner."""
    canonical = cast("str", row["canonical_definition"])
    try:
        definition = StrategyDefinition.model_validate_json(canonical)
    except ValidationError as error:
        raise StrategySnapshotError("Strategy snapshot content failed validation.") from error
    if canonical_strategy_bytes(definition).decode("utf-8") != canonical:
        raise StrategySnapshotError("Strategy snapshot bytes are not canonical.")
    if strategy_fingerprint(definition) != strategy_fingerprint_value:
        raise StrategySnapshotError("Strategy snapshot fingerprint verification failed.")
    owner = cast("str | None", row["strategy_id"])
    if owner is not None and owner != str(definition.strategy_id):
        raise StrategySnapshotError("Strategy snapshot owner does not match its document.")
    return StrategySnapshot(strategy_fingerprint=strategy_fingerprint_value, definition=definition)


async def _strategy_row(
    connection: AsyncConnection, strategy_id: UUID, *, lock: bool
) -> _LockedStrategy:
    """Read (and optionally row-lock) the strategy being deleted."""
    statement = select(strategies.c.strategy_id, strategies.c.name).where(
        strategies.c.strategy_id == str(strategy_id)
    )
    if lock:
        statement = statement.with_for_update()
    row = (await connection.execute(statement)).mappings().one_or_none()
    if row is None:
        raise StrategyNotFoundError("Strategy was not found.")
    return _LockedStrategy(
        strategy_id=cast("str", row["strategy_id"]), name=cast("str", row["name"])
    )


async def _blocking_deployments(
    connection: AsyncConnection, strategy_id: str, *, lock: bool = False
) -> tuple[UUID, ...]:
    """Return running or paused deployments of the strategy (row-locked when deleting)."""
    statement = (
        select(deployments.c.id)
        .where(
            deployments.c.strategy_id == strategy_id,
            deployments.c.status.in_(_ACTIVE_STATUSES),
        )
        .order_by(deployments.c.id)
    )
    if lock:
        statement = statement.with_for_update()
    return tuple(cast("UUID", value) for value in (await connection.execute(statement)).scalars())


async def _count(connection: AsyncConnection, statement: Select[tuple[int]]) -> int:
    """Execute one COUNT query."""
    return int((await connection.execute(statement)).scalar_one())


def _studies_condition(strategy_id: str) -> ColumnElement[bool]:
    """Select every study the strategy owns or takes part in."""
    members = select(research_study_strategies.c.study_fingerprint).where(
        research_study_strategies.c.strategy_id == strategy_id
    )
    return or_(
        published_research_studies.c.strategy_id == strategy_id,
        published_research_studies.c.study_fingerprint.in_(members),
    )


def _kept_snapshot_condition(strategy_id: str) -> ColumnElement[bool]:
    """The strategy's snapshots that no kept (live) deployment references."""
    referenced = select(deployments.c.strategy_fingerprint).where(
        deployments.c.strategy_fingerprint.is_not(None),
        deployments.c.mode == "live",
    )
    return (strategy_snapshots.c.strategy_id == strategy_id) & (
        strategy_snapshots.c.strategy_fingerprint.not_in(referenced)
    )


async def _deletion_counts(connection: AsyncConnection, strategy_id: str) -> StrategyDeletionCounts:
    """Count every row a deletion removes or detaches."""

    def by_strategy(table: Table) -> Select[tuple[int]]:
        """Count one research table's rows for the strategy."""
        return select(func.count()).select_from(table).where(table.c.strategy_id == strategy_id)

    def books(mode: str) -> Select[tuple[int]]:
        """Count the strategy's deployments in one mode."""
        return (
            select(func.count())
            .select_from(deployments)
            .where(deployments.c.strategy_id == strategy_id, deployments.c.mode == mode)
        )

    return StrategyDeletionCounts(
        snapshots=await _count(
            connection,
            select(func.count())
            .select_from(strategy_snapshots)
            .where(_kept_snapshot_condition(strategy_id)),
        ),
        backtests=await _count(connection, by_strategy(published_backtest_results)),
        research_runs=await _count(connection, by_strategy(published_research_run_specs)),
        studies=await _count(
            connection,
            select(func.count())
            .select_from(published_research_studies)
            .where(_studies_condition(strategy_id)),
        ),
        research_jobs=await _count(connection, by_strategy(research_jobs)),
        dataset_bindings=await _count(connection, by_strategy(strategy_dataset_bindings)),
        paper_deployments=await _count(connection, books("paper")),
        live_deployments_kept=await _count(connection, books("live")),
        allocations_removed=await _allocation_count(connection, UUID(strategy_id)),
        portfolio_sleeves=await count_strategy_sleeves(connection, strategy_id),
    )


async def _allocation_count(connection: AsyncConnection, strategy_id: UUID) -> int:
    """Return how many active risk-policy allocations reserve capital for the strategy."""
    active = await load_active_policy_in(connection)
    return sum(1 for item in active.definition.allocations if item.strategy_id == strategy_id)


async def _delete_paper_books(connection: AsyncConnection, strategy_id: str) -> None:
    """Delete the strategy's paper deployments with their complete ledgers."""
    paper_ids = select(deployments.c.id).where(
        deployments.c.strategy_id == strategy_id, deployments.c.mode == "paper"
    )
    await connection.execute(
        update(execution_orders)
        .where(execution_orders.c.deployment_id.in_(paper_ids))
        .values(parent_order_id=None)
    )
    for table in (
        trade_reason_records,
        execution_fills,
        execution_orders,
        order_intents,
        execution_positions,
        execution_instrument_state,
    ):
        await connection.execute(delete(table).where(table.c.deployment_id.in_(paper_ids)))
    await connection.execute(
        delete(deployments).where(
            deployments.c.strategy_id == strategy_id, deployments.c.mode == "paper"
        )
    )


async def _delete_research(connection: AsyncConnection, strategy_id: str) -> None:
    """Delete jobs, studies, results, run specs, and bindings in dependency order."""
    await connection.execute(
        delete(research_jobs).where(research_jobs.c.strategy_id == strategy_id)
    )
    await connection.execute(
        delete(published_research_studies).where(_studies_condition(strategy_id))
    )
    await connection.execute(
        delete(published_backtest_results).where(
            published_backtest_results.c.strategy_id == strategy_id
        )
    )
    await connection.execute(
        delete(published_research_run_specs).where(
            published_research_run_specs.c.strategy_id == strategy_id
        )
    )
    await connection.execute(
        delete(strategy_dataset_bindings).where(
            strategy_dataset_bindings.c.strategy_id == strategy_id
        )
    )


async def _delete_unreferenced_snapshots(connection: AsyncConnection, strategy_id: str) -> None:
    """Delete the strategy's snapshots except those a kept live deployment ran."""
    await connection.execute(
        delete(strategy_snapshots).where(_kept_snapshot_condition(strategy_id))
    )


async def _remove_allocation(connection: AsyncConnection, strategy_id: UUID) -> bool:
    """Publish the next risk-policy version without the deleted strategy's allocation."""
    active = await load_active_policy_in(connection, for_update=True)
    successor = successor_without_allocation(active, strategy_id)
    if successor is None:
        return False
    await publish_policy_in(connection, successor)
    return True


def _verify_compatible_dataset(
    snapshot: StrategySnapshot,
    dataset_fingerprint: str,
    dataset_store: DatasetStore,
) -> None:
    """Verify immutable dataset availability and strategy identity compatibility.

    A multi-instrument document (ADR 0056) runs the same timeframes on every covered
    product, so a dataset matches when its product is any covered product and its
    timeframe is one the document reads.
    """
    try:
        manifest = dataset_store.load_manifest(dataset_fingerprint)
    except (DatasetStoreError, OSError, ValueError) as error:
        raise StrategySnapshotError(
            "Immutable dataset could not be verified for strategy binding."
        ) from error
    definition = snapshot.definition
    allowed_products = covered_product_ids(definition)
    allowed_timeframes = {
        requirement.timeframe for requirement in expanded_data_requirements(definition)
    }
    if (
        manifest.provider != "coinbase"
        or manifest.product_id not in allowed_products
        or manifest.timeframe not in allowed_timeframes
    ):
        raise StrategyDatasetMismatchError(
            f"Dataset {manifest.product_id} {manifest.timeframe} ({manifest.provider}) does not "
            f"match the strategy: it covers {', '.join(allowed_products)} on "
            f"{', '.join(sorted(allowed_timeframes))} (coinbase)."
        )


def _validate_fingerprint(value: str, *, label: str) -> None:
    """Reject malformed content identities before filesystem or SQL lookup."""
    if _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise StrategySnapshotError(f"Invalid {label} fingerprint.")


def _journal_instant() -> datetime:
    """The UTC millisecond the portfolio journal records for a deletion's sleeve removals."""
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1_000) * 1_000)


def _require_utc(value: datetime) -> None:
    """Require timezone-aware UTC timestamps."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise StrategyLibraryError("Strategy timestamps must be UTC.")


def snapshot_owner(strategy_fingerprint_value: str) -> ScalarSelect[str | None]:
    """Scalar subquery resolving a snapshot fingerprint to its owning strategy_id.

    Research and runtime rows copy their ``strategy_id`` from the snapshot they
    reference, so a row can never claim a strategy its rules did not come from.
    A detached snapshot (owner deleted) yields NULL and the NOT NULL insert fails.
    """
    return (
        select(strategy_snapshots.c.strategy_id)
        .where(strategy_snapshots.c.strategy_fingerprint == strategy_fingerprint_value)
        .scalar_subquery()
    )
