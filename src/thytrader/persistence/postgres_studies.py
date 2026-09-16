"""PostgreSQL repository for immutable composed research-study catalog rows."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.market_data.models import DatasetTimeframe
from thytrader.persistence.schema import published_research_studies
from thytrader.research.catalog import (
    StudyCatalogIntegrityError,
    StudyCatalogNotFoundError,
    StudyCatalogSummary,
    StudyCatalogUnavailableError,
)
from thytrader.research.parameter_sweep import SelectionMetric

if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncEngine

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_STUDY_KINDS = frozenset(
    {
        "oos_holdout",
        "walk_forward",
        "cross_market",
        "parameter_sweep",
        "walk_forward_optimization",
    }
)


class PostgresResearchStudyCatalog:
    """Append and reverify canonical study documents."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the catalog to a managed async engine."""
        self._engine = engine

    async def persist(
        self,
        summary: StudyCatalogSummary,
        canonical_study: str,
    ) -> StudyCatalogSummary:
        """Idempotently store one assembled study after identity checks."""
        _validate_fingerprint(summary.study_fingerprint)
        statement = (
            insert(published_research_studies)
            .values(
                study_fingerprint=summary.study_fingerprint,
                request_fingerprint=summary.request_fingerprint,
                kind=summary.kind,
                engine_contract_version=summary.engine_contract_version,
                product_id=summary.product_id,
                timeframe=summary.timeframe,
                window_count=summary.window_count,
                selected_strategy_fingerprint=summary.selected_strategy_fingerprint,
                mean_oos_return_fraction=summary.mean_oos_return_fraction,
                stitched_oos_available=summary.stitched_oos_available,
                selection_metric=(
                    summary.selection_metric.value if summary.selection_metric is not None else None
                ),
                canonical_study=canonical_study,
                published_at=summary.published_at,
            )
            .on_conflict_do_nothing()
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise StudyCatalogUnavailableError("Research study catalog is unavailable.") from error
        loaded = await self.load(summary.study_fingerprint)
        if loaded != canonical_study:
            raise StudyCatalogIntegrityError(
                "Published research study content failed integrity verification."
            )
        return summary

    async def load(self, study_fingerprint: str) -> str:
        """Load one canonical study document and reverify its fingerprint."""
        _validate_fingerprint(study_fingerprint)
        statement = select(published_research_studies.c.canonical_study).where(
            published_research_studies.c.study_fingerprint == study_fingerprint
        )
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).one_or_none()
        except SQLAlchemyError as error:
            raise StudyCatalogUnavailableError("Research study catalog is unavailable.") from error
        if row is None:
            raise StudyCatalogNotFoundError("Published research study was not found.")
        canonical = cast("str", row[0])
        _verify_stored_fingerprint(study_fingerprint, canonical)
        return canonical

    async def list_summaries(
        self,
        *,
        kind: str | None = None,
        limit: int = 50,
    ) -> tuple[StudyCatalogSummary, ...]:
        """Return newest-first catalog rows without child ledgers."""
        if kind is not None and kind not in _STUDY_KINDS:
            raise StudyCatalogIntegrityError("Unknown research study kind.")
        if limit < 1 or limit > 100:
            raise StudyCatalogIntegrityError("Study catalog limit must be between 1 and 100.")
        table = published_research_studies
        statement = (
            select(
                table.c.study_fingerprint,
                table.c.request_fingerprint,
                table.c.kind,
                table.c.engine_contract_version,
                table.c.product_id,
                table.c.timeframe,
                table.c.window_count,
                table.c.selected_strategy_fingerprint,
                table.c.mean_oos_return_fraction,
                table.c.stitched_oos_available,
                table.c.selection_metric,
                table.c.published_at,
            )
            .order_by(table.c.published_at.desc(), table.c.study_fingerprint.asc())
            .limit(limit)
        )
        if kind is not None:
            statement = statement.where(table.c.kind == kind)
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise StudyCatalogUnavailableError("Research study catalog is unavailable.") from error
        return tuple(_summary_from_row(row) for row in rows)


def _summary_from_row(row: RowMapping) -> StudyCatalogSummary:
    """Build one catalog summary from an indexed row."""
    metric = row["selection_metric"]
    try:
        return StudyCatalogSummary(
            study_fingerprint=cast("str", row["study_fingerprint"]),
            request_fingerprint=cast("str", row["request_fingerprint"]),
            kind=cast("str", row["kind"]),
            engine_contract_version=cast("str", row["engine_contract_version"]),
            published_at=row["published_at"],
            product_id=cast("str", row["product_id"]),
            timeframe=cast("DatasetTimeframe", row["timeframe"]),
            window_count=cast("int", row["window_count"]),
            selected_strategy_fingerprint=cast("str | None", row["selected_strategy_fingerprint"]),
            mean_oos_return_fraction=cast("str | None", row["mean_oos_return_fraction"]),
            stitched_oos_available=cast("bool | None", row["stitched_oos_available"]),
            selection_metric=SelectionMetric(metric) if isinstance(metric, str) else None,
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise StudyCatalogIntegrityError(
            "Published research study row failed validation."
        ) from error


def _validate_fingerprint(value: str) -> None:
    """Reject malformed study identities before querying PostgreSQL."""
    if _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise StudyCatalogIntegrityError("Study fingerprints must be sha256 identities.")


def _verify_stored_fingerprint(study_fingerprint: str, canonical_study: str) -> None:
    """Reject a stored document whose embedded identity does not match the row key."""
    try:
        payload = json.loads(canonical_study)
    except json.JSONDecodeError as error:
        raise StudyCatalogIntegrityError(
            "Published research study content failed integrity verification."
        ) from error
    if not isinstance(payload, dict) or payload.get("study_fingerprint") != study_fingerprint:
        raise StudyCatalogIntegrityError(
            "Published research study content failed integrity verification."
        )
