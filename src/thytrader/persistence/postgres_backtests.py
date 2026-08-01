"""PostgreSQL repository for immutable deterministic backtest result publication."""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError
from pydantic_core import PydanticSerializationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.backtest.models import (
    BacktestResult,
    backtest_result_fingerprint,
    canonical_backtest_result_bytes,
)
from thytrader.persistence.schema import published_backtest_results, published_research_run_specs

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class BacktestPublicationError(RuntimeError):
    """Report a redacted immutable-result persistence or integrity failure."""


class PostgresBacktestResultStore:
    """Append and reverify canonical results that are derived from published run artifacts."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Use one application-managed asynchronous PostgreSQL engine."""
        self._engine = engine

    async def publish(self, result: BacktestResult) -> BacktestResult:
        """Idempotently append one result after canonical and source-row identity verification."""
        validated = _validated_result(result)
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

    async def load(self, result_fingerprint_value: str) -> BacktestResult:
        """Load one result and reverify canonical bytes, identity rows, and source linkage."""
        _validate_fingerprint(result_fingerprint_value)
        statement = select(
            published_backtest_results.c.run_fingerprint,
            published_backtest_results.c.strategy_fingerprint,
            published_backtest_results.c.dataset_fingerprint,
            published_backtest_results.c.signal_trace_fingerprint,
            published_backtest_results.c.canonical_result,
        ).where(published_backtest_results.c.result_fingerprint == result_fingerprint_value)
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise BacktestPublicationError("Backtest result storage is unavailable.") from error
        if row is None:
            raise BacktestPublicationError("Published backtest result was not found.")
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
        if backtest_result_fingerprint(result) != result_fingerprint_value:
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

    async def _verify_source_identity(self, result: BacktestResult) -> None:
        """Require source fingerprints to match their existing immutable run publication row."""
        statement = select(
            published_research_run_specs.c.strategy_fingerprint,
            published_research_run_specs.c.dataset_fingerprint,
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


def _validated_result(result: BacktestResult) -> BacktestResult:
    """Round-trip an unchecked typed result before issuing database queries or inserts."""
    try:
        return BacktestResult.model_validate_json(canonical_backtest_result_bytes(result))
    except (PydanticSerializationError, TypeError, ValueError, ValidationError) as error:
        raise BacktestPublicationError("Backtest result is invalid.") from error


def _validate_fingerprint(value: str) -> None:
    """Reject malformed result identities before issuing a SQL query."""
    if not isinstance(value, str) or _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise BacktestPublicationError("Invalid backtest result fingerprint.")
