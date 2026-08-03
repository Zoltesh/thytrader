"""PostgreSQL repository for immutable research-run specification publication."""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError
from pydantic_core import PydanticSerializationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.market_data.datasets import DatasetStoreError
from thytrader.persistence.postgres_strategies import PostgresStrategyPublicationStore
from thytrader.persistence.schema import published_research_run_specs
from thytrader.research.models import (
    ResearchRunSpecification,
    canonical_research_run_bytes,
    research_run_fingerprint,
)
from thytrader.research.publication import (
    PublishedResearchRunSpecification,
    ResearchRunPublicationError,
    verify_research_run_eligibility,
)
from thytrader.strategies.publication import StrategyPublicationError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.market_data.datasets import DatasetStore

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class PostgresResearchRunStore:
    """Publish and reverify immutable research-run specifications in PostgreSQL."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Use one application-managed asynchronous database engine."""
        self._engine = engine
        self._strategy_store = PostgresStrategyPublicationStore(engine)

    async def publish(
        self,
        specification: ResearchRunSpecification,
        *,
        dataset_store: DatasetStore,
        execution_fingerprint: str | None = None,
    ) -> PublishedResearchRunSpecification:
        """Idempotently publish one canonical specification after exact artifact verification."""
        validated = _validated_specification(specification)
        await self._verify_artifacts(validated, dataset_store)
        canonical = canonical_research_run_bytes(validated).decode("utf-8")
        fingerprint = research_run_fingerprint(validated)
        statement = (
            insert(published_research_run_specs)
            .values(
                run_fingerprint=fingerprint,
                run_id=str(validated.run_id),
                created_at=validated.created_at,
                strategy_fingerprint=validated.strategy_fingerprint,
                dataset_fingerprint=validated.dataset_fingerprint,
                canonical_specification=canonical,
                execution_fingerprint=execution_fingerprint,
                published_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing()
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ResearchRunPublicationError(
                "Research run publication storage is unavailable."
            ) from error
        try:
            published = await self.load(fingerprint, dataset_store=dataset_store)
        except ResearchRunPublicationError as error:
            if "was not found" not in str(error):
                raise
            identity_statement = select(published_research_run_specs.c.run_fingerprint).where(
                published_research_run_specs.c.run_id == str(validated.run_id)
            )
            try:
                async with self._engine.connect() as connection:
                    existing_fingerprint = (
                        await connection.execute(identity_statement)
                    ).scalar_one_or_none()
            except SQLAlchemyError as storage_error:
                raise ResearchRunPublicationError(
                    "Research run publication storage is unavailable."
                ) from storage_error
            if existing_fingerprint is not None:
                raise ResearchRunPublicationError(
                    "This research run identity was already published with different content."
                ) from error
            raise
        if published.specification != validated:
            raise ResearchRunPublicationError(
                "Published research run content failed integrity verification."
            )
        return published

    async def load_by_execution_fingerprint(
        self,
        execution_fingerprint: str,
        *,
        dataset_store: DatasetStore,
    ) -> PublishedResearchRunSpecification | None:
        """Return the one previously published run for exact executable semantics, if any."""
        _validate_fingerprint(execution_fingerprint)
        statement = select(published_research_run_specs.c.run_fingerprint).where(
            published_research_run_specs.c.execution_fingerprint == execution_fingerprint
        )
        try:
            async with self._engine.connect() as connection:
                fingerprint = (await connection.execute(statement)).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise ResearchRunPublicationError(
                "Research run publication storage is unavailable."
            ) from error
        if fingerprint is None:
            return None
        return await self.load(cast("str", fingerprint), dataset_store=dataset_store)

    async def load(
        self,
        run_fingerprint_value: str,
        *,
        dataset_store: DatasetStore,
    ) -> PublishedResearchRunSpecification:
        """Load one canonical specification and reverify its row and immutable inputs."""
        _validate_fingerprint(run_fingerprint_value)
        statement = select(
            published_research_run_specs.c.run_id,
            published_research_run_specs.c.created_at,
            published_research_run_specs.c.strategy_fingerprint,
            published_research_run_specs.c.dataset_fingerprint,
            published_research_run_specs.c.canonical_specification,
        ).where(published_research_run_specs.c.run_fingerprint == run_fingerprint_value)
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise ResearchRunPublicationError(
                "Research run publication storage is unavailable."
            ) from error
        if row is None:
            raise ResearchRunPublicationError("Published research run was not found.")

        canonical = cast("str", row["canonical_specification"])
        try:
            specification = ResearchRunSpecification.model_validate_json(canonical)
            canonical_matches = (
                canonical_research_run_bytes(specification).decode("utf-8") == canonical
            )
        except (PydanticSerializationError, TypeError, ValueError, ValidationError) as error:
            raise ResearchRunPublicationError(
                "Published research run content failed validation."
            ) from error
        if not canonical_matches:
            raise ResearchRunPublicationError("Published research run bytes are not canonical.")
        if research_run_fingerprint(specification) != run_fingerprint_value:
            raise ResearchRunPublicationError(
                "Published research run fingerprint verification failed."
            )
        if (
            cast("str", row["run_id"]) != str(specification.run_id)
            or cast("datetime", row["created_at"]) != specification.created_at
            or cast("str", row["strategy_fingerprint"]) != specification.strategy_fingerprint
            or cast("str", row["dataset_fingerprint"]) != specification.dataset_fingerprint
        ):
            raise ResearchRunPublicationError(
                "Published research run row identity does not match its canonical document."
            )
        await self._verify_artifacts(specification, dataset_store)
        return PublishedResearchRunSpecification(
            run_fingerprint=run_fingerprint_value,
            specification=specification,
        )

    async def _verify_artifacts(
        self,
        specification: ResearchRunSpecification,
        dataset_store: DatasetStore,
    ) -> None:
        """Reverify the exact strategy, binding, dataset, and eligibility contract."""
        try:
            await self._strategy_store.load_binding(
                specification.strategy_fingerprint,
                specification.dataset_fingerprint,
                dataset_store=dataset_store,
            )
            published_strategy = await self._strategy_store.load(specification.strategy_fingerprint)
            manifest = dataset_store.load_manifest(specification.dataset_fingerprint)
        except (DatasetStoreError, OSError, StrategyPublicationError, ValueError) as error:
            raise ResearchRunPublicationError(
                "Research run artifact binding could not be verified."
            ) from error
        verify_research_run_eligibility(specification, published_strategy, manifest)


def _validated_specification(
    specification: ResearchRunSpecification,
) -> ResearchRunSpecification:
    """Round-trip untrusted typed instances before artifact or database access."""
    try:
        return ResearchRunSpecification.model_validate_json(
            canonical_research_run_bytes(specification)
        )
    except (PydanticSerializationError, TypeError, ValueError, ValidationError) as error:
        raise ResearchRunPublicationError("Research run specification is invalid.") from error


def _validate_fingerprint(value: str) -> None:
    """Reject malformed run identities before issuing a SQL query."""
    if not isinstance(value, str) or _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise ResearchRunPublicationError("Invalid research run fingerprint.")
