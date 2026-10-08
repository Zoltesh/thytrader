"""PostgreSQL repository for immutable deterministic backtest result publication."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
import re
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

from pydantic import ValidationError
from pydantic_core import PydanticSerializationError
from sqlalchemy import JSON, cast as sql_cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.backtest.cost_attribution import compute_cost_attribution
from thytrader.backtest.metrics import compute_performance_metrics
from thytrader.backtest.models import (
    BacktestDiagnostics,
    BacktestEvaluationWindow,
    BacktestResult,
    BacktestSummary,
    backtest_evaluation_window,
    backtest_result_fingerprint,
    canonical_backtest_diagnostics_bytes,
    canonical_backtest_result_bytes,
)
from thytrader.backtest.results import (
    BacktestResultIntegrityError,
    BacktestResultNotFoundError,
    BacktestResultSummaryView,
    BacktestResultUnavailableError,
)
from thytrader.evaluation.models import ResearchRunSpecification
from thytrader.evaluation.publication import ResearchRunPublicationError
from thytrader.evaluation.trace import SignalTrace, signal_trace_fingerprint
from thytrader.persistence.backtest_projections import load_backtest_projections
from thytrader.persistence.postgres_strategy_snapshots import snapshot_owner
from thytrader.persistence.schema import published_backtest_results, published_research_run_specs

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.backtest.projections import BacktestProjection
    from thytrader.evaluation.publication import PublishedResearchRunSpecification
    from thytrader.market_data.datasets import DatasetStore

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_logger = logging.getLogger(__name__)


class BacktestPublicationError(RuntimeError):
    """Report a redacted immutable-result persistence or integrity failure."""


class BacktestResearchRunReader(Protocol):
    """Read one fully verified immutable research run for result consumers."""

    async def load(
        self,
        run_fingerprint_value: str,
        *,
        dataset_store: DatasetStore,
    ) -> PublishedResearchRunSpecification:
        """Load and reverify one published run against its immutable dataset."""
        ...


class PostgresBacktestResultStore:
    """Append and reverify canonical results that are derived from published run artifacts."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        research_run_store: BacktestResearchRunReader,
        dataset_store: DatasetStore,
    ) -> None:
        """Use one application-managed engine and a mandatory full source verifier."""
        self._engine = engine
        self._research_run_store = research_run_store
        self._dataset_store = dataset_store

    async def publish(
        self,
        result: BacktestResult,
        *,
        trace: SignalTrace,
        diagnostics: BacktestDiagnostics | None = None,
    ) -> BacktestResult:
        """Idempotently append one result after canonical source and trace verification.

        Diagnostics live in ``diagnostics_json`` beside the canonical bytes. Republishing
        an identical result only fills diagnostics a pre-0055 row never recorded; it never
        rewrites the canonical result or replaces recorded diagnostics. Derived metrics
        and fee attribution likewise fill absent metadata without replacing recorded evidence.
        """
        validated = _validated_result(result)
        _verify_trace_identity(validated, trace)
        await self._verify_source_identity(validated)
        canonical = canonical_backtest_result_bytes(validated).decode("utf-8")
        fingerprint = backtest_result_fingerprint(validated)
        diagnostics_json = (
            None
            if diagnostics is None
            else canonical_backtest_diagnostics_bytes(diagnostics).decode("utf-8")
        )
        metrics_json = await asyncio.to_thread(_publication_metrics_json, validated)
        attribution = await asyncio.to_thread(compute_cost_attribution, validated)
        inserted = insert(published_backtest_results).values(
            result_fingerprint=fingerprint,
            run_fingerprint=validated.run_fingerprint,
            strategy_fingerprint=validated.strategy_fingerprint,
            strategy_id=snapshot_owner(validated.strategy_fingerprint),
            dataset_fingerprint=validated.dataset_fingerprint,
            signal_trace_fingerprint=validated.signal_trace_fingerprint,
            canonical_result=canonical,
            metrics_json=metrics_json,
            cost_attribution_json=attribution.model_dump_json(),
            published_at=datetime.now(UTC),
            diagnostics_json=diagnostics_json,
        )
        statement = inserted.on_conflict_do_update(
            index_elements=[published_backtest_results.c.result_fingerprint],
            set_={
                "cost_attribution_json": func.coalesce(
                    published_backtest_results.c.cost_attribution_json,
                    inserted.excluded.cost_attribution_json,
                ),
                "metrics_json": func.coalesce(
                    published_backtest_results.c.metrics_json, inserted.excluded.metrics_json
                ),
                "diagnostics_json": func.coalesce(
                    published_backtest_results.c.diagnostics_json,
                    inserted.excluded.diagnostics_json,
                ),
            },
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise BacktestPublicationError("Backtest result storage is unavailable.") from error
        loaded = await self.load(fingerprint)
        if loaded != validated:
            raise BacktestPublicationError(
                "Published backtest result content failed integrity verification."
            )
        return loaded

    async def load_projections(
        self, result_fingerprints: tuple[str, ...]
    ) -> tuple[BacktestProjection, ...]:
        """Read bounded authenticated publications without materializing their ledgers."""
        return await load_backtest_projections(self._engine, result_fingerprints)

    async def load_diagnostics(self, result_fingerprint: str) -> BacktestDiagnostics | None:
        """Return the entry-funnel counters stored beside one result, or None when absent.

        Diagnostics explain a result; they never authenticate it. A row written before
        Alembic 0055, or a column that no longer validates, reads as None (logged).
        """
        _validate_fingerprint(result_fingerprint)
        statement = select(published_backtest_results.c.diagnostics_json).where(
            published_backtest_results.c.result_fingerprint == result_fingerprint
        )
        try:
            async with self._engine.connect() as connection:
                stored = (await connection.execute(statement)).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise BacktestPublicationError("Backtest result storage is unavailable.") from error
        if stored is None:
            return None
        try:
            return BacktestDiagnostics.model_validate_json(cast("str", stored))
        except ValidationError:
            _logger.warning("Stored backtest diagnostics failed validation; reporting none.")
            return None

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Load one result and reverify canonical bytes, identity rows, and source linkage."""
        _validate_fingerprint(result_fingerprint)
        statement = select(
            published_backtest_results.c.run_fingerprint,
            published_backtest_results.c.strategy_fingerprint,
            published_backtest_results.c.dataset_fingerprint,
            published_backtest_results.c.signal_trace_fingerprint,
            published_backtest_results.c.canonical_result,
        ).where(published_backtest_results.c.result_fingerprint == result_fingerprint)
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise BacktestPublicationError("Backtest result storage is unavailable.") from error
        if row is None:
            raise BacktestResultNotFoundError("Published backtest result was not found.")
        canonical = cast("str", row["canonical_result"])
        try:
            result = BacktestResult.model_validate_json(canonical)
            canonical_matches = canonical_backtest_result_bytes(result).decode("utf-8") == canonical
        except (PydanticSerializationError, TypeError, ValueError, ValidationError) as error:
            raise BacktestPublicationError(
                "Published backtest result content failed validation."
            ) from error
        if not canonical_matches:
            raise BacktestPublicationError("Published backtest result bytes are not canonical.")
        if backtest_result_fingerprint(result) != result_fingerprint:
            raise BacktestPublicationError(
                "Published backtest result fingerprint verification failed."
            )
        if (
            cast("str", row["run_fingerprint"]) != result.run_fingerprint
            or cast("str", row["strategy_fingerprint"]) != result.strategy_fingerprint
            or cast("str", row["dataset_fingerprint"]) != result.dataset_fingerprint
            or cast("str", row["signal_trace_fingerprint"]) != result.signal_trace_fingerprint
        ):
            raise BacktestPublicationError(
                "Published backtest result row identity does not match its canonical document."
            )
        await self._verify_source_identity(result)
        return result

    async def list_fingerprints(
        self,
        *,
        run_fingerprint: str | None = None,
        strategy_fingerprint: str | None = None,
        limit: int,
    ) -> tuple[str, ...]:
        """List immutable result identities in stable publication order without mutation."""
        if run_fingerprint is not None:
            _validate_fingerprint(run_fingerprint)
        if strategy_fingerprint is not None:
            _validate_fingerprint(strategy_fingerprint)
        if run_fingerprint is not None and strategy_fingerprint is not None:
            raise BacktestPublicationError("Result discovery accepts one source filter at a time.")
        if limit < 1 or limit > 100:
            raise BacktestPublicationError("Result discovery limit must be between 1 and 100.")
        statement = (
            select(published_backtest_results.c.result_fingerprint)
            .order_by(
                published_backtest_results.c.published_at.desc(),
                published_backtest_results.c.result_fingerprint.asc(),
            )
            .limit(limit)
        )
        if run_fingerprint is not None:
            statement = statement.where(
                published_backtest_results.c.run_fingerprint == run_fingerprint
            )
        if strategy_fingerprint is not None:
            statement = statement.where(
                published_backtest_results.c.strategy_fingerprint == strategy_fingerprint
            )
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).scalars().all()
        except SQLAlchemyError as error:
            raise BacktestPublicationError("Backtest result storage is unavailable.") from error
        return tuple(cast("str", row) for row in rows)

    async def list_summaries(
        self,
        *,
        run_fingerprint: str | None = None,
        strategy_fingerprint: str | None = None,
        dataset_fingerprint: str | None = None,
        strategy_id: UUID | None = None,
        limit: int,
        offset: int,
    ) -> tuple[BacktestResultSummaryView, ...]:
        """Return bounded newest-first summary rows without loading full ledgers.

        Summary metrics are extracted from the canonical document's immutable
        ``summary`` block server-side; identity columns come from the indexed
        row. The source run is joined so each row states its evaluated window
        (ADR 0094). At most one source filter is accepted per query.
        """
        filters = [
            value
            for value in (run_fingerprint, strategy_fingerprint, dataset_fingerprint)
            if value is not None
        ]
        if len(filters) + (strategy_id is not None) > 1:
            raise BacktestPublicationError("Summary discovery accepts one source filter at a time.")
        for value in filters:
            _validate_fingerprint(value)
        if limit < 1 or limit > 101:
            raise BacktestPublicationError("Summary discovery limit must be between 1 and 101.")
        if offset < 0:
            raise BacktestPublicationError("Summary discovery offset must not be negative.")

        table = published_backtest_results
        runs = published_research_run_specs
        summary_json = sql_cast(table.c.canonical_result, JSON)["summary"].label("summary")
        statement = (
            select(
                table.c.result_fingerprint,
                table.c.run_fingerprint,
                table.c.strategy_fingerprint,
                table.c.strategy_id,
                table.c.dataset_fingerprint,
                table.c.published_at,
                summary_json,
                runs.c.canonical_specification,
            )
            .select_from(table.outerjoin(runs, runs.c.run_fingerprint == table.c.run_fingerprint))
            .order_by(table.c.published_at.desc(), table.c.result_fingerprint.asc())
            .limit(limit)
            .offset(offset)
        )
        if run_fingerprint is not None:
            statement = statement.where(table.c.run_fingerprint == run_fingerprint)
        if strategy_fingerprint is not None:
            statement = statement.where(table.c.strategy_fingerprint == strategy_fingerprint)
        if dataset_fingerprint is not None:
            statement = statement.where(table.c.dataset_fingerprint == dataset_fingerprint)
        if strategy_id is not None:
            statement = statement.where(table.c.strategy_id == str(strategy_id))
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise BacktestResultUnavailableError(
                "Backtest result storage is unavailable."
            ) from error
        return tuple(_to_summary_view(row) for row in rows)

    async def newest_summaries_for_strategy_ids(
        self,
        strategy_ids: Sequence[UUID],
    ) -> dict[UUID, BacktestResultSummaryView]:
        """Return the newest summary per strategy in one ``DISTINCT ON`` round trip.

        A library page costs a single indexed query regardless of page size.
        Strategies without stored results are absent from the mapping.
        """
        if not strategy_ids:
            return {}
        if len(strategy_ids) > 1000:
            raise BacktestPublicationError("Summary discovery accepts at most 1000 strategies.")
        table = published_backtest_results
        summary_json = sql_cast(table.c.canonical_result, JSON)["summary"].label("summary")
        statement = (
            select(
                table.c.result_fingerprint,
                table.c.run_fingerprint,
                table.c.strategy_fingerprint,
                table.c.strategy_id,
                table.c.dataset_fingerprint,
                table.c.published_at,
                summary_json,
            )
            .where(table.c.strategy_id.in_([str(item) for item in strategy_ids]))
            .distinct(table.c.strategy_id)
            .order_by(
                table.c.strategy_id,
                table.c.published_at.desc(),
                table.c.result_fingerprint.asc(),
            )
        )
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise BacktestResultUnavailableError(
                "Backtest result storage is unavailable."
            ) from error
        newest: dict[UUID, BacktestResultSummaryView] = {}
        for row in rows:
            view = _to_summary_view(row)
            if view.strategy_id is not None:
                newest[UUID(view.strategy_id)] = view
        return newest

    async def load_source_specification(
        self,
        result: BacktestResult,
    ) -> ResearchRunSpecification:
        """Load and reverify the immutable research run behind one result."""
        try:
            published = await self._research_run_store.load(
                result.run_fingerprint,
                dataset_store=self._dataset_store,
            )
        except ResearchRunPublicationError as error:
            raise BacktestPublicationError(
                "Backtest result source run could not be fully verified."
            ) from error
        if published.run_fingerprint != result.run_fingerprint:
            raise BacktestPublicationError(
                "Backtest result source verifier returned a different run identity."
            )
        specification = published.specification
        if (
            specification.strategy_fingerprint != result.strategy_fingerprint
            or specification.dataset_fingerprint != result.dataset_fingerprint
            or specification.engine != result.engine
        ):
            raise BacktestPublicationError("Backtest result source run does not match the result.")
        return specification

    async def _verify_source_identity(self, result: BacktestResult) -> None:
        """Require source fingerprints to match their existing immutable run publication row."""
        await self.load_source_specification(result)


def _verify_trace_identity(result: BacktestResult, trace: SignalTrace) -> None:
    """Require a trace emitted for the exact result source identities and engine."""
    if (
        signal_trace_fingerprint(trace) != result.signal_trace_fingerprint
        or trace.run_fingerprint != result.run_fingerprint
        or trace.strategy_fingerprint != result.strategy_fingerprint
        or trace.dataset_fingerprint != result.dataset_fingerprint
        or trace.engine != result.engine
    ):
        raise BacktestPublicationError("Backtest result trace does not match verified sources.")


def _validated_result(result: BacktestResult) -> BacktestResult:
    """Round-trip an unchecked typed result before issuing database queries or inserts."""
    try:
        return BacktestResult.model_validate_json(canonical_backtest_result_bytes(result))
    except (PydanticSerializationError, TypeError, ValueError, ValidationError) as error:
        raise BacktestPublicationError("Backtest result is invalid.") from error


def _to_summary_view(row: object) -> BacktestResultSummaryView:
    """Project one indexed row plus its extracted summary into a discovery view."""
    mapping = cast("Mapping[str, object]", row)
    try:
        summary = BacktestSummary.model_validate(mapping["summary"])
    except (TypeError, ValueError, ValidationError) as error:
        raise BacktestResultIntegrityError(
            "Stored backtest result summary failed validation."
        ) from error
    published_at = mapping["published_at"]
    if not isinstance(published_at, datetime):
        raise BacktestResultIntegrityError("Stored backtest result publication time is invalid.")
    return BacktestResultSummaryView(
        result_fingerprint=cast("str", mapping["result_fingerprint"]),
        run_fingerprint=cast("str", mapping["run_fingerprint"]),
        strategy_fingerprint=cast("str", mapping["strategy_fingerprint"]),
        dataset_fingerprint=cast("str", mapping["dataset_fingerprint"]),
        published_at=published_at,
        summary=summary,
        strategy_id=cast("str | None", mapping.get("strategy_id")),
        window=_summary_window(mapping.get("canonical_specification"), summary),
    )


def _summary_window(
    canonical_specification: object, summary: BacktestSummary
) -> BacktestEvaluationWindow | None:
    """Derive one listed result's evaluated window from its joined run (display only).

    Detail reads reverify the run; a listing only explains which bars a row covers, so
    an unreadable run reports no window instead of failing the whole page.
    """
    if not isinstance(canonical_specification, str):
        return None
    try:
        specification = ResearchRunSpecification.model_validate_json(canonical_specification)
        return backtest_evaluation_window(specification, summary.evaluation_bars)
    except TypeError, ValueError, ValidationError:
        _logger.warning("Backtest summary window could not be derived from its run")
        return None


def _validate_fingerprint(value: str) -> None:
    """Reject malformed result identities before issuing a SQL query."""
    if not isinstance(value, str) or _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise BacktestPublicationError("Invalid backtest result fingerprint.")


def _publication_metrics_json(result: BacktestResult) -> str | None:
    """Leave undefined advisory ratios absent without rejecting valid ledger evidence."""
    try:
        return compute_performance_metrics(result).model_dump_json()
    except ValueError:
        return None
