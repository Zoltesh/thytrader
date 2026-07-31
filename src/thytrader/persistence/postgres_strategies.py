"""PostgreSQL repository for immutable strategy publication and dataset binding."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError
from pydantic_core import PydanticSerializationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.market_data.datasets import DatasetStoreError
from thytrader.persistence.schema import published_strategy_versions, strategy_dataset_bindings
from thytrader.strategies.models import (
    StrategyDefinition,
    StrategyStatus,
    canonical_strategy_bytes,
    strategy_fingerprint,
)
from thytrader.strategies.publication import (
    PublishedStrategy,
    StrategyDatasetBinding,
    StrategyPublicationError,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.market_data.datasets import DatasetStore

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class PostgresStrategyPublicationStore:
    """Publish and verify immutable strategy versions in PostgreSQL."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Use one application-managed asynchronous database engine."""
        self._engine = engine

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
        canonical = cast("str", row["canonical_definition"])
        try:
            definition = StrategyDefinition.model_validate_json(canonical)
        except ValidationError as error:
            raise StrategyPublicationError(
                "Published strategy content failed validation."
            ) from error
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
