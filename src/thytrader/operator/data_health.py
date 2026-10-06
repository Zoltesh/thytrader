"""Clock-aware watched-market freshness, separate from historical completeness."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from thytrader.execution.candle_wait import NEWEST_BAR_SETTLE_SECONDS
from thytrader.market_data.models import DatasetTimeframe, parse_candle_interval
from thytrader.operator.models import (
    ComponentReport,
    DataCatalogReport,
    DatasetCoverageRow,
    OperatorEnvelope,
    ReportStatus,
)

TailState = Literal["fresh", "settling", "stale", "missing", "invalid"]


class WatchedTail(BaseModel):
    """One published data tail; worker success and historical completion are independent."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    product_id: str
    timeframe: DatasetTimeframe
    provider: str | None
    expected_closed_end: datetime
    covered_ends_at: datetime | None
    tail_state: TailState
    lag_seconds: int | None = Field(ge=0)
    missing_closed_bars: int | None = Field(ge=0)
    settlement_deadline: datetime
    watch_complete: bool | None
    island_complete: bool | None
    worker_status: str | None
    failure_code: str | None


class DataHealthPayload(BaseModel):
    """All enabled watches, not the default product and not an execution-readiness gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal["enabled_watchlist_published_datasets"] = "enabled_watchlist_published_datasets"
    settlement_grace_seconds: int = NEWEST_BAR_SETTLE_SECONDS
    inventory_complete: bool
    watched_count: int
    attention_count: int
    datasets: tuple[WatchedTail, ...]


class DataHealthReport(OperatorEnvelope):
    """Read-only all-market tail report preserving unavailable-catalog evidence."""

    report_kind: Literal["data_health"] = "data_health"
    payload: DataHealthPayload


def watched_tail(row: DatasetCoverageRow, *, now: datetime) -> WatchedTail:
    """Measure a published exclusive close against its own clock, never wall-clock age alone."""
    interval = parse_candle_interval(row.timeframe)
    expected = interval.align_closed_end(now)
    deadline = expected + timedelta(seconds=NEWEST_BAR_SETTLE_SECONDS)
    end = row.covered_ends_at
    state: TailState
    lag: int | None = None
    missing: int | None = None
    if end is None:
        state = "missing"
    elif (
        end.tzinfo is None
        or end.utcoffset() != timedelta(0)
        or end > expected
        or interval.align_closed_end(end) != end
    ):
        state = "invalid"
    else:
        lag = int((expected - end).total_seconds())
        missing = (expected - end) // interval.duration
        if missing == 0:
            state = "fresh"
        elif missing == 1 and now < deadline:
            state = "settling"
        else:
            state = "stale"
    return WatchedTail(
        product_id=row.product_id,
        timeframe=row.timeframe,
        provider=row.provider,
        expected_closed_end=expected,
        covered_ends_at=end,
        tail_state=state,
        lag_seconds=lag,
        missing_closed_bars=missing,
        settlement_deadline=deadline,
        watch_complete=row.watch_complete,
        island_complete=row.island_complete,
        worker_status=row.worker_status,
        failure_code=row.failure_code,
    )


def data_health_report(catalog: DataCatalogReport) -> DataHealthReport:
    """Project the existing verified catalog without fetching candles or creating watches."""
    rows = tuple(
        watched_tail(row, now=catalog.generated_at)
        for row in sorted(
            catalog.payload.datasets,
            key=lambda row: (row.product_id, row.timeframe, row.provider or ""),
        )
        if row.watched
    )
    attention = sum(row.tail_state in {"stale", "missing", "invalid"} for row in rows)
    complete = (
        not catalog.partial_result_warnings and catalog.overall_status is ReportStatus.HEALTHY
    )
    status = catalog.overall_status
    if status is ReportStatus.HEALTHY and (attention or not complete):
        status = ReportStatus.DEGRADED
    component = ComponentReport(
        name="watched_data_tails",
        status=status,
        reason_code="TAILS_NEED_ATTENTION" if attention else ("OK" if complete else "PARTIAL"),
        detail=(
            f"{len(rows)} enabled watched series; {attention} stale, missing or invalid tails. "
            "Fresh tails do not prove complete historical coverage or execution readiness."
        ),
    )
    return DataHealthReport(
        **catalog.model_dump(
            exclude={
                "report_kind",
                "payload",
                "components",
                "overall_status",
                "recommended_next_action",
            }
        ),
        components=(*catalog.components, component),
        overall_status=status,
        recommended_next_action=(
            "Inspect affected watches with thytrader-data inspect-gaps; do not treat worker "
            "success or island completeness as fresh data."
            if attention or not complete
            else "No stale watched tails. Check watch_complete separately before research."
        ),
        payload=DataHealthPayload(
            inventory_complete=complete,
            watched_count=len(rows),
            attention_count=attention,
            datasets=rows,
        ),
    )
