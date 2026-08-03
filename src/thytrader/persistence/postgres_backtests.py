"""PostgreSQL repository for immutable deterministic backtest result publication."""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError
from pydantic_core import PydanticSerializationError
from sqlalchemy import JSON, cast as sql_cast, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.backtest.models import (
    BacktestResult,
    BacktestSummary,
    backtest_result_fingerprint,
    canonical_backtest_result_bytes,
)
from thytrader.persistence.backtest_results import (
    BacktestResultIntegrityError,
    BacktestResultNotFoundError,
    BacktestResultSummaryView,
    BacktestResultUnavailableError,
)
from thytrader.persistence.schema import published_backtest_results, published_research_run_specs
from thytrader.research.models import (
    ResearchRunSpecification,
    canonical_research_run_bytes,
    research_run_fingerprint,
)
from thytrader.research.publication import ResearchRunPublicationError
from thytrader.research.trace import SignalTrace, signal_trace_fingerprint

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.market_data.datasets import DatasetStore
    from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class BacktestPublicationError(RuntimeError):
    """Report a redacted immutable-result persistence or integrity failure."""


class PostgresBacktestResultStore:
    """Append and reverify canonical results that are derived from published run artifacts."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        research_run_store: PostgresResearchRunStore | None = None,
        dataset_store: DatasetStore | None = None,
    ) -> None:
        """Use one application-managed engine and optional full source verifier."""
        if (research_run_store is None) != (dataset_store is None):
            raise ValueError("Research-run and dataset stores must be configured together.")
        self._engine = engine
        self._research_run_store = research_run_store
        self._dataset_store = dataset_store

    async def publish(self, result: BacktestResult, *, trace: SignalTrace) -> BacktestResult:
        """Idempotently append one result after canonical source and trace verification."""
        validated = _validated_result(result)
        _verify_trace_identity(validated, trace)
        await self._verify_source_identity(validated)
        canonical = canonical_backtest_result_bytes(validated).decode("utf-8")
        fingerprint = backtest_result_fingerprint(validated)
        statement = (
            insert(published_backtest_results)
            .values(
                result_fingerprint=fingerprint,
                run_fingerprint=validated.run_fingerprint,
                strategy_fingerprint=validated.strategy_fingerprint,
                dataset_fingerprint=validated.dataset_fingerprint,
                signal_trace_fingerprint=validated.signal_trace_fingerprint,
                canonical_result=canonical,
                published_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing()
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
        limit: int,
        offset: int,
    ) -> tuple[BacktestResultSummaryView, ...]:
        """Return bounded newest-first summary rows without loading full ledgers.

        Summary metrics are extracted from the canonical document's immutable
        ``summary`` block server-side; identity columns come from the indexed
        row. At most one source filter is accepted per query.
        """
        filters = [
            value
            for value in (run_fingerprint, strategy_fingerprint, dataset_fingerprint)
            if value is not None
        ]
        if len(filters) > 1:
            raise BacktestPublicationError("Summary discovery accepts one source filter at a time.")
        for value in filters:
            _validate_fingerprint(value)
        if limit < 1 or limit > 100:
            raise BacktestPublicationError("Summary discovery limit must be between 1 and 100.")
        if offset < 0:
            raise BacktestPublicationError("Summary discovery offset must not be negative.")

        table = published_backtest_results
        summary_json = sql_cast(table.c.canonical_result, JSON)["summary"].label("summary")
        engine_contract_version = (
            sql_cast(table.c.canonical_result, JSON)["engine_contract_version"]
            .as_string()
            .label("engine_contract_version")
        )
        statement = (
            select(
                table.c.result_fingerprint,
                table.c.run_fingerprint,
                table.c.strategy_fingerprint,
                table.c.dataset_fingerprint,
                table.c.published_at,
                engine_contract_version,
                summary_json,
            )
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
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise BacktestResultUnavailableError(
                "Backtest result storage is unavailable."
            ) from error
        return tuple(_to_summary_view(row) for row in rows)

    async def load_source_specification(
        self,
        result: BacktestResult,
    ) -> ResearchRunSpecification:
        """Load and reverify the immutable research run behind one result."""
        if self._research_run_store is not None and self._dataset_store is not None:
            try:
                published = await self._research_run_store.load(
                    result.run_fingerprint,
                    dataset_store=self._dataset_store,
                )
            except ResearchRunPublicationError as error:
                raise BacktestPublicationError(
                    "Backtest result source run could not be fully verified."
                ) from error
            specification = published.specification
            if (
                specification.strategy_fingerprint != result.strategy_fingerprint
                or specification.dataset_fingerprint != result.dataset_fingerprint
                or specification.engine_contract_version != result.engine_contract_version
                or specification.broker != result.broker
            ):
                raise BacktestPublicationError(
                    "Backtest result source run does not match the result."
                )
            return specification
        statement = select(
            published_research_run_specs.c.run_id,
            published_research_run_specs.c.created_at,
            published_research_run_specs.c.strategy_fingerprint,
            published_research_run_specs.c.dataset_fingerprint,
            published_research_run_specs.c.canonical_specification,
        ).where(published_research_run_specs.c.run_fingerprint == result.run_fingerprint)
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise BacktestPublicationError(
                "Backtest result source publication is unavailable."
            ) from error
        if row is None:
            raise BacktestPublicationError("Backtest result source run was not found.")
        if (
            cast("str", row["strategy_fingerprint"]) != result.strategy_fingerprint
            or cast("str", row["dataset_fingerprint"]) != result.dataset_fingerprint
        ):
            raise BacktestPublicationError(
                "Backtest result source identities do not match the run."
            )
        canonical = cast("str", row["canonical_specification"])
        try:
            specification = ResearchRunSpecification.model_validate_json(canonical)
        except (TypeError, ValueError, ValidationError) as error:
            raise BacktestPublicationError("Backtest result source run is invalid.") from error
        if (
            cast("str", row["run_id"]) != str(specification.run_id)
            or cast("datetime", row["created_at"]) != specification.created_at
            or specification.strategy_fingerprint != result.strategy_fingerprint
            or specification.dataset_fingerprint != result.dataset_fingerprint
            or specification.engine_contract_version != result.engine_contract_version
            or specification.broker != result.broker
            or canonical_research_run_bytes(specification).decode("utf-8") != canonical
            or research_run_fingerprint(specification) != result.run_fingerprint
        ):
            raise BacktestPublicationError(
                "Backtest result source run is not a canonical executable backtest run."
            )
        return specification

    async def _verify_source_identity(self, result: BacktestResult) -> None:
        """Require source fingerprints to match their existing immutable run publication row."""
        await self.load_source_specification(result)


def _verify_trace_identity(result: BacktestResult, trace: SignalTrace) -> None:
    """Require a trace emitted for the exact result source identities and engine contract."""
    if (
        signal_trace_fingerprint(trace) != result.signal_trace_fingerprint
        or trace.run_fingerprint != result.run_fingerprint
        or trace.strategy_fingerprint != result.strategy_fingerprint
        or trace.dataset_fingerprint != result.dataset_fingerprint
        or trace.engine_contract_version != result.engine_contract_version
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
        engine_contract_version=cast("str", mapping["engine_contract_version"]),
        published_at=published_at,
        summary=summary,
    )


def _validate_fingerprint(value: str) -> None:
    """Reject malformed result identities before issuing a SQL query."""
    if not isinstance(value, str) or _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise BacktestPublicationError("Invalid backtest result fingerprint.")
