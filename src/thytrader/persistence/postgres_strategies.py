"""PostgreSQL repository for immutable strategy publication and dataset binding."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re
from typing import TYPE_CHECKING, Never, cast

from pydantic import ValidationError
from pydantic_core import PydanticSerializationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.market_data.datasets import DatasetStoreError
from thytrader.persistence.schema import (
    archived_strategy_versions,
    published_strategy_versions,
    strategy_dataset_bindings,
    strategy_drafts,
)
from thytrader.strategies.authoring import StrategyDraft
from thytrader.strategies.models import (
    StrategyDefinition,
    StrategyStatus,
    canonical_strategy_bytes,
    strategy_fingerprint,
)
from thytrader.strategies.publication import (
    PublishedStrategy,
    StrategyCatalogEntry,
    StrategyDatasetBinding,
    StrategyPublicationError,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from thytrader.market_data.datasets import DatasetStore

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class PostgresStrategyPublicationStore:
    """Publish and verify immutable strategy versions in PostgreSQL."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Use one application-managed asynchronous database engine."""
        self._engine = engine

    async def create_draft(self, definition: StrategyDefinition) -> StrategyDraft:
        """Persist one validated editable draft under its immutable draft identity."""
        canonical = _canonical_draft(definition)
        statement = (
            insert(strategy_drafts)
            .values(
                strategy_id=str(definition.strategy_id),
                version=definition.version,
                created_at=definition.created_at,
                updated_at=datetime.now(UTC),
                revision=1,
                canonical_definition=canonical,
            )
            .on_conflict_do_nothing()
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
            return await self._load_draft(definition.strategy_id, definition.version)
        except SQLAlchemyError as error:
            raise RuntimeError("Strategy draft storage is unavailable.") from error

    async def list_drafts(self) -> tuple[StrategyDraft, ...]:
        """Load and validate every saved draft in stable creation order."""
        statement = select(
            strategy_drafts.c.strategy_id,
            strategy_drafts.c.version,
            strategy_drafts.c.created_at,
            strategy_drafts.c.revision,
            strategy_drafts.c.canonical_definition,
        ).order_by(strategy_drafts.c.created_at.asc(), strategy_drafts.c.strategy_id.asc())
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("Strategy draft storage is unavailable.") from error
        return tuple(_validate_draft_row(row) for row in rows)

    async def save_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> StrategyDraft:
        """Replace a matching draft only when its durable revision is current."""
        canonical = _canonical_draft(definition)
        statement = (
            strategy_drafts.update()
            .where(
                strategy_drafts.c.strategy_id == str(definition.strategy_id),
                strategy_drafts.c.version == definition.version,
                strategy_drafts.c.created_at == definition.created_at,
                strategy_drafts.c.revision == expected_revision,
            )
            .values(
                canonical_definition=canonical,
                updated_at=datetime.now(UTC),
                revision=expected_revision + 1,
            )
        )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
            if result.rowcount != 1:
                if await self._draft_exists(definition.strategy_id, definition.version):
                    raise RuntimeError("Strategy draft revision conflict.")
                raise RuntimeError("Strategy draft was not found.")
            return await self._load_draft(definition.strategy_id, definition.version)
        except SQLAlchemyError as error:
            raise RuntimeError("Strategy draft storage is unavailable.") from error

    async def publish_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> PublishedStrategy:
        """Atomically save, publish, verify, and consume one current draft."""
        try:
            validated_draft = StrategyDefinition.model_validate_json(
                canonical_strategy_bytes(definition)
            )
        except (PydanticSerializationError, TypeError, ValueError) as error:
            raise StrategyPublicationError("Strategy draft is invalid.") from error
        if validated_draft.status is not StrategyStatus.DRAFT:
            raise StrategyPublicationError("Only draft strategy definitions can be published.")
        published_definition = StrategyDefinition.model_validate(
            {
                **validated_draft.model_dump(mode="python"),
                "status": StrategyStatus.PUBLISHED,
            }
        )
        try:
            async with self._engine.begin() as connection:
                return await self._publish_draft_transaction(
                    connection,
                    validated_draft,
                    published_definition,
                    expected_revision=expected_revision,
                )
        except StrategyPublicationError:
            raise
        except SQLAlchemyError as error:
            raise StrategyPublicationError(
                "Strategy publication storage is unavailable."
            ) from error

    async def _publish_draft_transaction(
        self,
        connection: AsyncConnection,
        draft: StrategyDefinition,
        published: StrategyDefinition,
        *,
        expected_revision: int,
    ) -> PublishedStrategy:
        """Execute the complete draft-to-publication transition in one DB transaction."""
        draft_canonical = canonical_strategy_bytes(draft).decode("utf-8")
        published_canonical = canonical_strategy_bytes(published).decode("utf-8")
        fingerprint = strategy_fingerprint(published)
        update_draft = (
            strategy_drafts.update()
            .where(
                strategy_drafts.c.strategy_id == str(draft.strategy_id),
                strategy_drafts.c.version == draft.version,
                strategy_drafts.c.created_at == draft.created_at,
                strategy_drafts.c.revision == expected_revision,
            )
            .values(
                canonical_definition=draft_canonical,
                updated_at=datetime.now(UTC),
                revision=expected_revision + 1,
            )
            .returning(strategy_drafts.c.revision)
        )
        publication_lookup = select(
            published_strategy_versions.c.strategy_id,
            published_strategy_versions.c.version,
            published_strategy_versions.c.created_at,
            published_strategy_versions.c.canonical_definition,
            published_strategy_versions.c.source_draft_revision,
        ).where(published_strategy_versions.c.strategy_fingerprint == fingerprint)
        updated_revision = (await connection.execute(update_draft)).scalar_one_or_none()
        if updated_revision is None:
            return await self._resolve_missing_publish_draft(
                connection,
                draft,
                published,
                expected_revision=expected_revision,
                expected_canonical=published_canonical,
                fingerprint=fingerprint,
            )
        await connection.execute(
            insert(published_strategy_versions)
            .values(
                strategy_fingerprint=fingerprint,
                strategy_id=str(published.strategy_id),
                version=published.version,
                created_at=published.created_at,
                canonical_definition=published_canonical,
                published_at=datetime.now(UTC),
                source_draft_revision=expected_revision,
            )
            .on_conflict_do_nothing()
        )
        persisted_row = (await connection.execute(publication_lookup)).mappings().one_or_none()
        if persisted_row is None:
            await self._raise_publication_identity_conflict(connection, published)
        verified = _published_strategy_from_row(persisted_row, fingerprint)
        if (
            verified.definition != published
            or cast("int | None", persisted_row["source_draft_revision"]) != expected_revision
        ):
            await self._raise_publication_identity_conflict(connection, published)
        deleted = await connection.execute(
            strategy_drafts.delete().where(
                strategy_drafts.c.strategy_id == str(draft.strategy_id),
                strategy_drafts.c.version == draft.version,
                strategy_drafts.c.revision == expected_revision + 1,
            )
        )
        if deleted.rowcount != 1:
            raise StrategyPublicationError("Strategy draft lifecycle changed during publication.")
        return PublishedStrategy(strategy_fingerprint=fingerprint, definition=published)

    async def _resolve_missing_publish_draft(
        self,
        connection: AsyncConnection,
        draft: StrategyDefinition,
        published: StrategyDefinition,
        *,
        expected_revision: int,
        expected_canonical: str,
        fingerprint: str,
    ) -> PublishedStrategy:
        """Distinguish stale, idempotent, and missing draft publication attempts."""
        current_revision = (
            await connection.execute(
                select(strategy_drafts.c.revision).where(
                    strategy_drafts.c.strategy_id == str(draft.strategy_id),
                    strategy_drafts.c.version == draft.version,
                )
            )
        ).scalar_one_or_none()
        if current_revision is not None:
            raise StrategyPublicationError("Strategy draft revision conflict.")
        existing_row = (
            (
                await connection.execute(
                    select(
                        published_strategy_versions.c.strategy_id,
                        published_strategy_versions.c.version,
                        published_strategy_versions.c.created_at,
                        published_strategy_versions.c.canonical_definition,
                        published_strategy_versions.c.source_draft_revision,
                    ).where(published_strategy_versions.c.strategy_fingerprint == fingerprint)
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing_row is not None:
            if cast("int | None", existing_row["source_draft_revision"]) != expected_revision:
                raise StrategyPublicationError("Strategy draft revision conflict.")
            verified = _published_strategy_from_row(existing_row, fingerprint)
            if (
                cast("str", existing_row["canonical_definition"]) == expected_canonical
                and verified.definition == published
            ):
                return verified
        raise StrategyPublicationError("Strategy draft was not found.")

    @staticmethod
    async def _raise_publication_identity_conflict(
        connection: AsyncConnection, published: StrategyDefinition
    ) -> Never:
        """Raise the controlled dual-unique publication failure for this identity."""
        existing_identity = (
            await connection.execute(
                select(published_strategy_versions.c.strategy_fingerprint).where(
                    published_strategy_versions.c.strategy_id == str(published.strategy_id),
                    published_strategy_versions.c.version == published.version,
                )
            )
        ).scalar_one_or_none()
        if existing_identity is not None:
            raise StrategyPublicationError(
                "This strategy identity and version was already published with different content."
            )
        raise StrategyPublicationError("Published strategy was not found.")

    async def delete_draft(self, strategy_id: UUID, version: int) -> None:
        """Consume one persisted draft after a matching immutable publication succeeds."""
        statement = strategy_drafts.delete().where(
            strategy_drafts.c.strategy_id == str(strategy_id),
            strategy_drafts.c.version == version,
        )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
        except SQLAlchemyError as error:
            raise RuntimeError("Strategy draft storage is unavailable.") from error
        if result.rowcount != 1:
            raise RuntimeError("Strategy draft was not found.")

    async def _draft_exists(self, strategy_id: UUID, version: int) -> bool:
        """Check whether a draft identity still exists after a failed CAS update."""
        statement = select(strategy_drafts.c.strategy_id).where(
            strategy_drafts.c.strategy_id == str(strategy_id),
            strategy_drafts.c.version == version,
        )
        async with self._engine.connect() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def _load_draft(self, strategy_id: UUID, version: int) -> StrategyDraft:
        """Load and validate one saved draft using its stable strategy identity."""
        statement = select(
            strategy_drafts.c.strategy_id,
            strategy_drafts.c.version,
            strategy_drafts.c.created_at,
            strategy_drafts.c.revision,
            strategy_drafts.c.canonical_definition,
        ).where(
            strategy_drafts.c.strategy_id == str(strategy_id),
            strategy_drafts.c.version == version,
        )
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().one_or_none()
        if row is None:
            raise RuntimeError("Strategy draft was not found.")
        return _validate_draft_row(row)

    async def list_published(self, *, include_archived: bool) -> tuple[StrategyCatalogEntry, ...]:
        """List verified published evidence, hiding permanently archived versions by default."""
        statement = (
            select(
                published_strategy_versions.c.strategy_fingerprint,
                archived_strategy_versions.c.archived_at,
            )
            .select_from(
                published_strategy_versions.outerjoin(
                    archived_strategy_versions,
                    archived_strategy_versions.c.strategy_fingerprint
                    == published_strategy_versions.c.strategy_fingerprint,
                )
            )
            .order_by(
                published_strategy_versions.c.published_at.desc(),
                published_strategy_versions.c.strategy_fingerprint.asc(),
            )
        )
        if not include_archived:
            statement = statement.where(archived_strategy_versions.c.archived_at.is_(None))
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("Strategy publication catalog is unavailable.") from error
        entries: list[StrategyCatalogEntry] = []
        for row in rows:
            fingerprint = cast("str", row["strategy_fingerprint"])
            published = await self.load(fingerprint)
            archived_at = cast("datetime | None", row["archived_at"])
            entries.append(
                StrategyCatalogEntry(
                    strategy_fingerprint=fingerprint,
                    definition=published.definition,
                    archived_at=archived_at,
                )
            )
        return tuple(entries)

    async def archive(self, strategy_fingerprint_value: str) -> StrategyCatalogEntry:
        """Write an idempotent permanent archive marker for verified immutable strategy evidence."""
        _validate_fingerprint(strategy_fingerprint_value, label="strategy")
        published = await self.load(strategy_fingerprint_value)
        statement = (
            insert(archived_strategy_versions)
            .values(
                strategy_fingerprint=strategy_fingerprint_value,
                archived_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing()
        )
        lookup = select(archived_strategy_versions.c.archived_at).where(
            archived_strategy_versions.c.strategy_fingerprint == strategy_fingerprint_value
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
                archived_at = (await connection.execute(lookup)).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise RuntimeError("Strategy publication catalog is unavailable.") from error
        if not isinstance(archived_at, datetime):
            raise TypeError("Strategy publication catalog archive timestamp is invalid.")
        return StrategyCatalogEntry(
            strategy_fingerprint=strategy_fingerprint_value,
            definition=published.definition,
            archived_at=archived_at,
        )

    async def publish(self, definition: StrategyDefinition) -> PublishedStrategy:
        """Idempotently publish one validated strategy version by canonical hash."""
        try:
            validated = StrategyDefinition.model_validate_json(canonical_strategy_bytes(definition))
        except (PydanticSerializationError, TypeError, ValueError) as error:
            raise StrategyPublicationError("Strategy definition is invalid.") from error
        if validated.status is not StrategyStatus.PUBLISHED:
            raise StrategyPublicationError("Only published strategy definitions can be persisted.")
        canonical = canonical_strategy_bytes(validated).decode("utf-8")
        fingerprint = strategy_fingerprint(validated)
        statement = (
            insert(published_strategy_versions)
            .values(
                strategy_fingerprint=fingerprint,
                strategy_id=str(validated.strategy_id),
                version=validated.version,
                created_at=validated.created_at,
                canonical_definition=canonical,
                published_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing()
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise StrategyPublicationError(
                "Strategy publication storage is unavailable."
            ) from error
        try:
            published = await self.load(fingerprint)
        except StrategyPublicationError as error:
            if "was not found" not in str(error):
                raise
            identity_statement = select(published_strategy_versions.c.strategy_fingerprint).where(
                published_strategy_versions.c.strategy_id == str(validated.strategy_id),
                published_strategy_versions.c.version == validated.version,
            )
            try:
                async with self._engine.connect() as connection:
                    existing_fingerprint = (
                        await connection.execute(identity_statement)
                    ).scalar_one_or_none()
            except SQLAlchemyError as storage_error:
                raise StrategyPublicationError(
                    "Strategy publication storage is unavailable."
                ) from storage_error
            if existing_fingerprint is not None:
                raise StrategyPublicationError(
                    "This strategy identity and version was already published "
                    "with different content."
                ) from error
            raise
        if published.definition != validated:
            raise StrategyPublicationError(
                "Published strategy content failed integrity verification."
            )
        return published

    async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
        """Load and cryptographically verify one exact published strategy document."""
        _validate_fingerprint(strategy_fingerprint_value, label="strategy")
        statement = select(
            published_strategy_versions.c.strategy_id,
            published_strategy_versions.c.version,
            published_strategy_versions.c.created_at,
            published_strategy_versions.c.canonical_definition,
        ).where(published_strategy_versions.c.strategy_fingerprint == strategy_fingerprint_value)
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise StrategyPublicationError(
                "Strategy publication storage is unavailable."
            ) from error
        if row is None:
            raise StrategyPublicationError("Published strategy was not found.")
        return _published_strategy_from_row(row, strategy_fingerprint_value)

    async def bind_dataset(
        self,
        strategy_fingerprint_value: str,
        dataset_fingerprint: str,
        *,
        dataset_store: DatasetStore,
        bound_at: datetime,
    ) -> StrategyDatasetBinding:
        """Idempotently bind exact verified strategy and dataset identities."""
        _require_utc(bound_at)
        published = await self.load(strategy_fingerprint_value)
        _validate_fingerprint(dataset_fingerprint, label="dataset")
        _verify_compatible_dataset(published, dataset_fingerprint, dataset_store)
        statement = (
            insert(strategy_dataset_bindings)
            .values(
                strategy_fingerprint=strategy_fingerprint_value,
                dataset_fingerprint=dataset_fingerprint,
                bound_at=bound_at,
            )
            .on_conflict_do_nothing(index_elements=["strategy_fingerprint", "dataset_fingerprint"])
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise StrategyPublicationError(
                "Strategy dataset binding storage is unavailable."
            ) from error
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
        published = await self.load(strategy_fingerprint_value)
        _verify_compatible_dataset(published, dataset_fingerprint, dataset_store)
        statement = select(strategy_dataset_bindings.c.bound_at).where(
            strategy_dataset_bindings.c.strategy_fingerprint == strategy_fingerprint_value,
            strategy_dataset_bindings.c.dataset_fingerprint == dataset_fingerprint,
        )
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise StrategyPublicationError(
                "Strategy dataset binding storage is unavailable."
            ) from error
        if row is None:
            raise StrategyPublicationError("Strategy dataset binding was not found.")
        return StrategyDatasetBinding(
            strategy_fingerprint=strategy_fingerprint_value,
            dataset_fingerprint=dataset_fingerprint,
            bound_at=cast("datetime", row["bound_at"]),
        )


def _canonical_draft(definition: StrategyDefinition) -> str:
    """Revalidate one draft and return canonical JSON safe for mutable draft storage."""
    try:
        validated = StrategyDefinition.model_validate_json(canonical_strategy_bytes(definition))
    except (PydanticSerializationError, TypeError, ValueError) as error:
        raise RuntimeError("Strategy draft is invalid.") from error
    if validated.status is not StrategyStatus.DRAFT:
        raise RuntimeError("Only draft strategy definitions can be persisted.")
    return canonical_strategy_bytes(validated).decode("utf-8")


def _published_strategy_from_row(
    row: RowMapping, strategy_fingerprint_value: str
) -> PublishedStrategy:
    """Validate one complete immutable publication row against its canonical identity."""
    canonical = cast("str", row["canonical_definition"])
    try:
        definition = StrategyDefinition.model_validate_json(canonical)
    except ValidationError as error:
        raise StrategyPublicationError("Published strategy content failed validation.") from error
    if definition.status is not StrategyStatus.PUBLISHED:
        raise StrategyPublicationError("Stored strategy does not have published status.")
    if canonical_strategy_bytes(definition).decode("utf-8") != canonical:
        raise StrategyPublicationError("Published strategy bytes are not canonical.")
    if strategy_fingerprint(definition) != strategy_fingerprint_value:
        raise StrategyPublicationError("Published strategy fingerprint verification failed.")
    if (
        cast("str", row["strategy_id"]) != str(definition.strategy_id)
        or cast("int", row["version"]) != definition.version
        or cast("datetime", row["created_at"]) != definition.created_at
    ):
        raise StrategyPublicationError(
            "Published strategy row identity does not match its canonical document."
        )
    return PublishedStrategy(
        strategy_fingerprint=strategy_fingerprint_value,
        definition=definition,
    )


def _validate_draft_row(row: RowMapping) -> StrategyDraft:
    """Verify stored draft bytes, identity, and optimistic-concurrency revision."""
    canonical = cast("str", row["canonical_definition"])
    try:
        definition = StrategyDefinition.model_validate_json(canonical)
    except ValidationError as error:
        raise RuntimeError("Stored strategy draft content failed validation.") from error
    if definition.status is not StrategyStatus.DRAFT:
        raise RuntimeError("Stored strategy draft does not have draft status.")
    if canonical_strategy_bytes(definition).decode("utf-8") != canonical:
        raise RuntimeError("Stored strategy draft bytes are not canonical.")
    if (
        cast("str", row["strategy_id"]) != str(definition.strategy_id)
        or cast("int", row["version"]) != definition.version
        or cast("datetime", row["created_at"]) != definition.created_at
    ):
        raise RuntimeError("Stored strategy draft identity does not match its canonical document.")
    revision = row["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise RuntimeError("Stored strategy draft revision is invalid.")
    return StrategyDraft(definition=definition, revision=revision)


def _verify_compatible_dataset(
    published: PublishedStrategy,
    dataset_fingerprint: str,
    dataset_store: DatasetStore,
) -> None:
    """Verify immutable dataset availability and strategy identity compatibility."""
    try:
        manifest = dataset_store.load_manifest(dataset_fingerprint)
    except (DatasetStoreError, OSError, ValueError) as error:
        raise StrategyPublicationError(
            "Immutable dataset could not be verified for strategy binding."
        ) from error
    if (
        manifest.provider != "coinbase"
        or manifest.product_id != published.definition.instrument.product_id
        or manifest.timeframe != published.definition.timeframe
    ):
        raise StrategyPublicationError(
            "Verified dataset identity does not match the published strategy."
        )


def _validate_fingerprint(value: str, *, label: str) -> None:
    """Reject malformed content identities before filesystem or SQL lookup."""
    if _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise StrategyPublicationError(f"Invalid {label} fingerprint.")


def _require_utc(value: datetime) -> None:
    """Require timezone-aware UTC association timestamps."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise StrategyPublicationError("Strategy dataset binding time must be UTC.")
