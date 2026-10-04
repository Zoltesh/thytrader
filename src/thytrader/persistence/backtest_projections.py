"""PostgreSQL publication-integrity projections for large immutable backtests.

PostgreSQL hashes the exact canonical bytes and extracts only small fields. Python
never receives trades/equity or reads Parquet on this presentation path. Full artifact
verification remains the separate result reader's responsibility (ADR 0109).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import JSON, String, cast, func, select
from sqlalchemy.exc import SQLAlchemyError

from thytrader.backtest.models import (
    BacktestDiagnostics,
    BacktestPerformanceMetrics,
    BacktestSummary,
    backtest_evaluation_window,
)
from thytrader.backtest.projections import BacktestProjection
from thytrader.persistence.backtest_results import (
    BacktestResultIntegrityError,
    BacktestResultNotFoundError,
    BacktestResultUnavailableError,
)
from thytrader.persistence.schema import published_backtest_results, published_research_run_specs
from thytrader.research.models import FingerprintText, ResearchRunSpecification

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.sql.elements import ColumnElement


class _ResultIdentity(BaseModel):
    """Small identity fields extracted from the fingerprint-authenticated result."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    run_fingerprint: FingerprintText
    strategy_fingerprint: FingerprintText
    dataset_fingerprint: FingerprintText
    signal_trace_fingerprint: FingerprintText
    engine: Literal["thytrader-backtest"]


class _ProjectionRow(BaseModel):
    """Validate a dynamic SQL row immediately before using it as publication evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    result_fingerprint: FingerprintText
    computed_result_fingerprint: FingerprintText
    computed_run_fingerprint: FingerprintText
    run_fingerprint: FingerprintText
    strategy_fingerprint: FingerprintText
    dataset_fingerprint: FingerprintText
    signal_trace_fingerprint: FingerprintText
    identity: _ResultIdentity
    summary: BacktestSummary
    canonical_specification: str
    diagnostics_json: str | None
    metrics_json: str | None


async def load_backtest_projections(
    engine: AsyncEngine, result_fingerprints: tuple[str, ...]
) -> tuple[BacktestProjection, ...]:
    """Load at most 100 exact publications in requested order in one bounded query."""
    if len(result_fingerprints) > 100 or len(set(result_fingerprints)) != len(result_fingerprints):
        raise BacktestResultIntegrityError("Projection requests require at most 100 unique ids.")
    if not result_fingerprints:
        return ()
    table = published_backtest_results
    runs = published_research_run_specs
    payload = cast(table.c.canonical_result, JSON)
    identities = _ResultIdentity.model_fields
    identity_fields = [item for key in identities for item in (key, payload[key].as_string())]
    statement = (
        select(
            table.c.result_fingerprint,
            table.c.run_fingerprint,
            table.c.strategy_fingerprint,
            table.c.dataset_fingerprint,
            table.c.signal_trace_fingerprint,
            func.json_build_object(*identity_fields).label("identity"),
            payload["summary"].label("summary"),
            _digest(table.c.canonical_result).label("computed_result_fingerprint"),
            _digest(runs.c.canonical_specification).label("computed_run_fingerprint"),
            runs.c.canonical_specification,
            table.c.diagnostics_json,
            table.c.metrics_json,
        )
        .select_from(table.join(runs, runs.c.run_fingerprint == table.c.run_fingerprint))
        .where(table.c.result_fingerprint.in_(result_fingerprints))
    )
    try:
        async with engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        projected = tuple(_projection(_ProjectionRow.model_validate(dict(row))) for row in rows)
    except SQLAlchemyError as error:
        raise BacktestResultUnavailableError("Backtest projections are unavailable.") from error
    except (ValueError, ValidationError) as error:
        raise BacktestResultIntegrityError("Backtest publication projection is invalid.") from error
    indexed = {row.result_fingerprint: row for row in projected}
    if len(indexed) != len(result_fingerprints):
        raise BacktestResultNotFoundError("Backtest publication or source was not found.")
    return tuple(indexed[fingerprint] for fingerprint in result_fingerprints)


def _digest(column: ColumnElement[str]) -> ColumnElement[str]:
    """Hash UTF-8 publication bytes inside PostgreSQL without another extension."""
    return func.concat(
        "sha256:", func.encode(func.sha256(func.convert_to(column, "UTF8")), "hex"), type_=String()
    )


def _projection(row: _ProjectionRow) -> BacktestProjection:
    """Validate content digests, row/source links and optional derived evidence."""
    if (
        row.result_fingerprint != row.computed_result_fingerprint
        or row.run_fingerprint != row.computed_run_fingerprint
    ):
        raise BacktestResultIntegrityError("Backtest publication digest does not match.")
    for key in (
        "run_fingerprint",
        "strategy_fingerprint",
        "dataset_fingerprint",
        "signal_trace_fingerprint",
    ):
        if getattr(row, key) != getattr(row.identity, key):
            raise BacktestResultIntegrityError("Backtest publication row identity does not match.")
    specification = ResearchRunSpecification.model_validate_json(row.canonical_specification)
    if (
        specification.strategy_fingerprint != row.strategy_fingerprint
        or specification.dataset_fingerprint != row.dataset_fingerprint
        or specification.engine != row.identity.engine
    ):
        raise BacktestResultIntegrityError("Backtest source identity does not match.")
    metrics = _metrics(row)
    return BacktestProjection(
        result_fingerprint=row.result_fingerprint,
        run_fingerprint=row.run_fingerprint,
        strategy_fingerprint=row.strategy_fingerprint,
        dataset_fingerprint=row.dataset_fingerprint,
        summary=row.summary,
        costs=specification.costs,
        metrics=metrics,
        diagnostics=_diagnostics(row.diagnostics_json),
        window=backtest_evaluation_window(specification, row.summary.evaluation_bars),
        warnings=()
        if metrics is not None
        else (
            "Derived metrics were not recorded for this publication; "
            "use the full result or metrics endpoint to calculate them.",
        ),
    )


def _metrics(row: _ProjectionRow) -> BacktestPerformanceMetrics | None:
    """Require a stored derived report's own digest and source identities to match."""
    if row.metrics_json is None:
        return None
    metrics = BacktestPerformanceMetrics.model_validate_json(row.metrics_json)
    if (
        metrics.result_fingerprint != row.result_fingerprint
        or metrics.run_fingerprint != row.run_fingerprint
    ):
        raise BacktestResultIntegrityError("Derived metrics source identity does not match.")
    return metrics


def _diagnostics(payload: str | None) -> BacktestDiagnostics | None:
    """Preserve the advisory null contract for absent or malformed diagnostics."""
    if payload is None:
        return None
    try:
        return BacktestDiagnostics.model_validate_json(payload)
    except ValidationError:
        return None
