"""Execution-worker cycle timing evidence (ADR 0131).

One ``ExecutionCycleReport`` describes one completed execution-worker cycle: its wall
duration against the configured interval, where the time went by phase, the slowest
books, the venue REST calls it made and the deploy-window cache state. Reports are
telemetry: building or storing one never stops or alters a cycle.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CyclePhaseName = Literal[
    "setup",
    "portfolio_supervision",
    "books",
    "risk_snapshots",
    "safety_supervision",
    "fleet_supervision",
]
CYCLE_PHASES: tuple[CyclePhaseName, ...] = (
    "setup",
    "portfolio_supervision",
    "books",
    "risk_snapshots",
    "safety_supervision",
    "fleet_supervision",
)
MAX_REPORTED_BOOKS = 10
MAX_REPORTED_ENDPOINTS = 15


class _CycleModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CyclePhaseTiming(_CycleModel):
    """Wall time, venue requests and database statements of one cycle phase.

    ``books`` is the per-book processing; ``risk_snapshots`` is the entry-gate evidence
    reload the cycle runs before and after every book, reported apart from ``books``.
    """

    name: CyclePhaseName
    seconds: float = Field(ge=0)
    venue_requests: int = Field(ge=0)
    venue_seconds: float = Field(ge=0)
    db_statements: int = Field(ge=0)
    db_seconds: float = Field(ge=0)


class CycleBookTiming(_CycleModel):
    """One book's processing time in a cycle, with the venue and database calls it made."""

    deployment_id: UUID
    product_id: str = Field(max_length=64)
    timeframe: str | None = Field(default=None, max_length=16)
    status: str = Field(max_length=16)
    mode: str = Field(max_length=16)
    kind: str = Field(max_length=16)
    seconds: float = Field(ge=0)
    venue_requests: int = Field(ge=0)
    venue_seconds: float = Field(ge=0)
    db_statements: int = Field(ge=0)
    db_seconds: float = Field(ge=0)
    window_range_requests: int = Field(ge=0)
    warming: bool = False
    failed: bool = False


class BookStatusCounts(_CycleModel):
    """Books the cycle visited, by lifecycle status at the start of the cycle."""

    running: int = Field(ge=0)
    paused: int = Field(ge=0)
    stopped: int = Field(ge=0)


class VenueEndpointTiming(_CycleModel):
    """Venue REST traffic to one endpoint shape (ids replaced by ``{id}``)."""

    method: str = Field(max_length=8)
    endpoint: str = Field(max_length=128)
    requests: int = Field(ge=0)
    errors: int = Field(ge=0)
    seconds: float = Field(ge=0)
    max_seconds: float = Field(ge=0)


class VenueCallSummary(_CycleModel):
    """Every venue REST call of one cycle, with the costliest endpoints listed."""

    requests: int = Field(ge=0)
    errors: int = Field(ge=0)
    seconds: float = Field(ge=0)
    max_seconds: float = Field(ge=0)
    endpoints: tuple[VenueEndpointTiming, ...] = Field(max_length=MAX_REPORTED_ENDPOINTS)


class DatabaseCallSummary(_CycleModel):
    """Every SQL statement of one cycle: count and summed latency (no SQL text)."""

    statements: int = Field(ge=0)
    seconds: float = Field(ge=0)


class WindowCacheCycleState(_CycleModel):
    """Deploy-window cache (ADR 0113) state after a cycle and its work during it.

    ``warming_books`` counts books whose window was still rebuilding within its request
    budget (no entries, retried next cycle); a cold cache after a restart shows here.
    """

    windows: int = Field(ge=0)
    cached_candles: int = Field(ge=0)
    range_requests: int = Field(ge=0)
    warming_events: int = Field(ge=0)
    warming_books: int = Field(ge=0)


class ExecutionCycleReport(_CycleModel):
    """Timing evidence for one completed execution-worker cycle."""

    cycle_id: UUID
    started_at: datetime
    completed_at: datetime
    duration_seconds: float = Field(ge=0)
    interval_seconds: int = Field(ge=1)
    deployments_listed: int = Field(ge=0)
    books: BookStatusCounts
    book_failures: int = Field(ge=0)
    slowest_phase: CyclePhaseName
    phases: tuple[CyclePhaseTiming, ...]
    slowest_books: tuple[CycleBookTiming, ...] = Field(max_length=MAX_REPORTED_BOOKS)
    venue: VenueCallSummary
    database: DatabaseCallSummary
    window_cache: WindowCacheCycleState

    @property
    def slow(self) -> bool:
        """True when the cycle took longer than its configured interval."""
        return self.duration_seconds > self.interval_seconds


class ExecutionCycleRecord(_CycleModel):
    """One stored cycle: started, and completed with its report once it finishes."""

    cycle_id: UUID
    started_at: datetime
    interval_seconds: int = Field(ge=1)
    completed_at: datetime | None = None
    report: ExecutionCycleReport | None = None
