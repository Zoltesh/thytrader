"""Shared contracts of one market-data ingest walk.

The provider range port and its dispatch, the heartbeat touch, a walk's context,
request budget and published island, its stop reasons and outcome, and the shared
worker logger. ``_logger`` keeps the historical
``thytrader.market_data_worker.service`` name so log records emitted by every
ingest module keep their original ``logger`` field.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import logging
from typing import TYPE_CHECKING, Literal, Protocol

from thytrader.market_data.hole_settlement import settle_cutoff
from thytrader.market_data.models import CandleInterval, CandleRangeReport
from thytrader.market_data.worker_state import (
    MarketDataWorkerAttempt,
    MarketDataWorkerError,
    MarketDataWorkerState,
    MarketDataWorkerStateStore,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from thytrader.market_data.datasets import DatasetStore
    from thytrader.market_data_worker.pacing import ProviderPacer
    from thytrader.persistence.worker_heartbeats import WorkerHeartbeatStore


_logger = logging.getLogger("thytrader.market_data_worker.service")


class IntervalRangeService(Protocol):
    """Provider-neutral 1h and 5m bounded historical range capability."""

    async def get_range(
        self,
        product_id: str,
        timeframe: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Return one validated explicit range for the requested interval."""
        ...


class HourlyRangeService(Protocol):
    """1h-only historical range stubs used by existing worker tests."""

    async def get_hourly_range(
        self,
        product_id: str,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Return one validated explicit hourly range."""
        ...


type HistoricalRangeService = IntervalRangeService | HourlyRangeService


class IngestStop(StrEnum):
    """Why one ``ingest_once`` call ended; the cycle scheduler reads it.

    ``UNSETTLED`` waits for a missing bar inside the settle window (Coinbase may still
    publish it). ``LISTING_FLOOR`` ends a backward walk whose listing search found no
    provider candle before the segment: the market had not traded yet (ADR 0095).
    ``INCONSISTENT`` stops on a confirmed page whose bounds, grid, or ordering disagree
    with its request; nothing is concluded from it.
    """

    CURRENT = "current"
    SKIPPED = "skipped"
    COMPLETE = "complete"
    BUDGET = "budget"
    UNSETTLED = "unsettled"
    LISTING_FLOOR = "listing_floor"
    INCONSISTENT = "inconsistent"
    RATE_LIMITED = "rate_limited"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """Result of one ``ingest_once`` call: its stop reason and provider requests spent."""

    stop: IngestStop
    requests: int = 0

    @property
    def more_work(self) -> bool:
        """True when the request budget ran out before the planned walk finished."""
        return self.stop is IngestStop.BUDGET


async def fetch_historical_range(
    service: HistoricalRangeService,
    product_id: str,
    timeframe: CandleInterval,
    starts_at: datetime,
    ends_at: datetime,
    now: datetime,
) -> CandleRangeReport:
    """Call ``get_range`` when present, otherwise the 1h-only stub method."""
    get_range = getattr(service, "get_range", None)
    if callable(get_range):
        return await get_range(product_id, timeframe, starts_at, ends_at, now)
    if timeframe is not CandleInterval.ONE_HOUR:
        raise MarketDataWorkerError("Historical provider does not support this timeframe.")
    get_hourly_range = getattr(service, "get_hourly_range", None)
    if not callable(get_hourly_range):
        raise MarketDataWorkerError("Historical provider does not support this timeframe.")
    return await get_hourly_range(product_id, starts_at, ends_at, now)


async def _touch_market_data_heartbeat(
    heartbeat_store: WorkerHeartbeatStore | None,
    now_factory: Callable[[], datetime] | None,
) -> None:
    """Record liveness with wall-clock time so a long cell cannot stale health."""
    if heartbeat_store is None:
        return
    instant = now_factory() if now_factory is not None else datetime.now(UTC)
    await heartbeat_store.touch("market_data_worker", instant.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class _Island:
    """The published complete island a walk extends, read from verified worker state."""

    state: MarketDataWorkerState
    fingerprint: str
    starts_at: datetime
    ends_at: datetime


@dataclass(slots=True)
class _RequestBudget:
    """Provider requests one ``ingest_once`` call may still spend; ``None`` is unbounded."""

    limit: int | None
    spent: int = 0

    def exhausted(self, *, allowance: int = 0) -> bool:
        """True when no request may start a new page, counting ``allowance`` extra requests."""
        return self.limit is not None and self.spent >= self.limit + allowance


@dataclass(frozen=True, slots=True)
class _WalkContext:
    """Dependencies, claimed attempt, and limits shared by one ingest walk."""

    service: HistoricalRangeService
    dataset_store: DatasetStore
    state_store: MarketDataWorkerStateStore
    provider: str
    product_id: str
    timeframe: CandleInterval
    closed_end: datetime
    attempt: MarketDataWorkerAttempt
    retry_at: datetime
    page_candles: int
    pacer: ProviderPacer
    budget: _RequestBudget
    heartbeat_store: WorkerHeartbeatStore | None
    now_factory: Callable[[], datetime] | None

    @property
    def bar(self) -> timedelta:
        """Return the duration of one bar of the walked timeframe."""
        return self.timeframe.duration

    @property
    def cutoff(self) -> datetime:
        """Return the instant from which missing bars are not yet confirmed no-trade bars."""
        return settle_cutoff(self.closed_end, self.timeframe)

    @property
    def page_span(self) -> timedelta:
        """Return the duration one provider page covers."""
        return self.timeframe.duration * self.page_candles

    @property
    def can_probe_days(self) -> bool:
        """True when the listing search may skip whole UTC days with daily-candle probes.

        A daily timeframe already pages by day, and a 1h-only provider stub cannot serve
        daily candles; both search with ordinary pages instead.
        """
        return self.timeframe is not CandleInterval.ONE_DAY and callable(
            getattr(self.service, "get_range", None)
        )

    def outcome(self, stop: IngestStop) -> IngestOutcome:
        """Return the call outcome with the requests this walk spent."""
        return IngestOutcome(stop, self.budget.spent)


type _Direction = Literal["forward", "prefix", "initial", "listing_probe"]
