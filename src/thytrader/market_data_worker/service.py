"""Core lifecycle for complete-only historical market-data publication.

Ingest walks fetch interval-aligned provider pages of at most
``HISTORICAL_REQUEST_MAX_CANDLES`` bars and publish one cumulative revision per walk
(ADR 0085). Initial backfill starts at the newest closed bar and walks back toward the
lookback start, so coverage is fresh after the first request. Incremental maintenance
walks forward from a one-bar overlap.

Coinbase returns no candle for an interval without trades. A settled missing bar that a
confirmation re-fetch still omits is a confirmed no-trade interval: it becomes a flat
zero-volume bar at the previous close, so a quiet minute never shrinks a series to its
newest island (ADR 0095). A missing bar inside the settle window is waited for. Only a
backward walk records ``history_floor_at``, and only when its listing search finds no
provider candle at all before the segment, back past the timeframe's lookback ceiling.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import IntEnum, StrEnum
import logging
import random
from typing import TYPE_CHECKING, Literal, Protocol

from thytrader.market_data.freshness import FreshnessStatus, evaluate_freshness
from thytrader.market_data.lookback import max_watch_lookback_hours
from thytrader.market_data.models import (
    HISTORICAL_REQUEST_MAX_CANDLES,
    MAX_HISTORICAL_INTERVAL_COUNT,
    CandleInterval,
    CandleRangeReport,
    MarketDataRateLimitedError,
)
from thytrader.market_data.no_trade import count_no_trade_bars, fill_no_trade_gaps, no_trade_bar
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.watchlist import (
    INGEST_REQUEST_POLL_SECONDS,
    MarketDataWatchlistStore,
    MarketDataWatchTarget,
)
from thytrader.market_data.worker_state import (
    MarketDataMaintenanceKind,
    MarketDataWorkerAttempt,
    MarketDataWorkerError,
    MarketDataWorkerFailure,
    MarketDataWorkerState,
    MarketDataWorkerStateStore,
    MarketDataWorkerSuccess,
    validate_market_data_worker_state,
)
from thytrader.market_data_worker.pacing import PROVIDER_REQUEST_PAUSE_SECONDS, ProviderPacer
from thytrader.market_data_worker.pages import (
    CandlePage,
    merge_confirmed_pages,
    run_end,
    settle_cutoff,
    split_page,
)

_logger = logging.getLogger(__name__)

# Fair share of provider requests one target may spend per worker cycle. A target with a
# pending ``ingest_requested_at`` gets the larger share; every due target is visited
# each cycle, so a long backfill never starves maintenance of the others.
INGEST_REQUESTS_PER_TARGET_CYCLE = 8
INGEST_REQUESTS_PER_REQUESTED_TARGET_CYCLE = 24
# Requests one walk may spend beyond its budget while it searches below the segment for
# the next provider candle. Such a search publishes nothing until it finds one, so a
# budget stop would repeat it every cycle. Its cost is bounded: pages to the UTC day
# boundary, then confirmed daily-granularity probes back to the listing horizon (at most
# eleven 350-day pages for a ten-year ceiling).
LISTING_SEARCH_REQUEST_ALLOWANCE = 48
# How far past the lookback ceiling a listing search looks: one page of daily candles.
_LISTING_SEARCH_MARGIN = timedelta(days=HISTORICAL_REQUEST_MAX_CANDLES)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from decimal import Decimal

    from thytrader.market_data.datasets import DatasetManifest, DatasetStore
    from thytrader.market_data.models import Candle
    from thytrader.market_data_worker.retention import DatasetRetentionRunner
    from thytrader.persistence.worker_heartbeats import WorkerHeartbeatStore
    from thytrader.settings_yaml import SettingsStore


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


# Stops after which a re-proven provider-history floor is settled for this process: the
# walk either reconciled, anchored the lookback, or re-ran the listing search.
_FLOOR_PROVEN_STOPS = frozenset({IngestStop.CURRENT, IngestStop.COMPLETE, IngestStop.LISTING_FLOOR})


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """Result of one ``ingest_once`` call: its stop reason and provider requests spent."""

    stop: IngestStop
    requests: int = 0

    @property
    def more_work(self) -> bool:
        """True when the request budget ran out before the planned walk finished."""
        return self.stop is IngestStop.BUDGET


async def _load_validated_state(
    state_store: MarketDataWorkerStateStore,
    provider: str,
    product_id: str,
    timeframe: CandleInterval,
) -> MarketDataWorkerState | None:
    """Load durable state and reject forged timestamps before scheduling."""
    state = await state_store.get(provider, product_id, timeframe)
    return validate_market_data_worker_state(state) if state is not None else None


def _planning_state(
    state: MarketDataWorkerState | None, *, reprove_history_floor: bool
) -> MarketDataWorkerState | None:
    """Return the state a walk plans from, without a floor that must be proven again.

    The durable row keeps its floor until a walk records a new outcome: the listing
    search re-records it, and a walk that finds older history moves the island start,
    which clears it.
    """
    if state is None or not reprove_history_floor or state.history_floor_at is None:
        return state
    return replace(state, history_floor_at=None)


async def ingest_once(
    *,
    service: HistoricalRangeService,
    dataset_store: DatasetStore,
    state_store: MarketDataWorkerStateStore,
    provider: str,
    product_id: str,
    lookback_hours: int,
    now: datetime,
    timeframe: CandleInterval = CandleInterval.ONE_HOUR,
    retry_base_seconds: int = 300,
    jitter_factory: Callable[[], float] = random.random,
    verify_current_dataset: bool = True,
    skip_reconcile: bool = False,
    heartbeat_store: WorkerHeartbeatStore | None = None,
    now_factory: Callable[[], datetime] | None = None,
    max_requests: int | None = None,
    max_candles_per_request: int = HISTORICAL_REQUEST_MAX_CANDLES,
    pacer: ProviderPacer | None = None,
    reprove_history_floor: bool = False,
) -> IngestOutcome:
    """Retrieve, verify, and publish complete coverage in provider-sized pages.

    ``max_requests`` bounds provider calls for this call (a confirmation re-fetch counts);
    ``None`` walks until the plan is done. ``max_candles_per_request`` defaults to the
    provider page limit; tests lower it to exercise multi-page walks on small fixtures.
    ``pacer`` spaces requests across targets; ``None`` uses an unpaced one.
    ``reprove_history_floor`` plans as if no floor were recorded, so the backward walk's
    listing search confirms it again (or replaces it with real history). The worker loop
    sets it on each target's first visit per process.
    """
    if max_candles_per_request < 2:
        raise ValueError("A provider page must hold an overlap bar plus one new bar.")
    ends_at = timeframe.align_closed_end(now)
    prior = _planning_state(
        await _load_validated_state(state_store, provider, product_id, timeframe),
        reprove_history_floor=reprove_history_floor,
    )
    watch_complete = island_covers_watch(
        covered_starts_at=None if prior is None else prior.covered_starts_at,
        covered_ends_at=None if prior is None else prior.covered_ends_at,
        island_complete=bool(prior is not None and prior.complete),
        lookback_hours=lookback_hours,
        interval=timeframe,
        closed_end=ends_at,
        product_id=product_id,
        now=now,
        history_floor_at=None if prior is None else prior.history_floor_at,
    )
    should_reconcile = not skip_reconcile and watch_complete
    if should_reconcile and await _reconcile_current_coverage(
        service=service,
        dataset_store=dataset_store,
        state_store=state_store,
        provider=provider,
        product_id=product_id,
        timeframe=timeframe,
        prior=prior,
        ends_at=ends_at,
        lookback_hours=lookback_hours,
        now=now,
        retry_base_seconds=retry_base_seconds,
        jitter_factory=jitter_factory,
        verify_current_dataset=verify_current_dataset,
    ):
        return IngestOutcome(IngestStop.CURRENT)
    starts_at, maintenance_kind = _plan_range(prior, ends_at, lookback_hours, timeframe)
    attempt = MarketDataWorkerAttempt(
        provider=provider,
        product_id=product_id,
        timeframe=timeframe,
        attempted_at=now.astimezone(UTC),
        requested_starts_at=starts_at,
        requested_ends_at=ends_at,
        maintenance_kind=maintenance_kind,
        expected_ends_at=ends_at,
        next_attempt_at=_safe_shift(
            now.astimezone(UTC),
            timedelta(seconds=retry_base_seconds),
            "Market-data worker cannot represent its next attempt time.",
        ),
        expected_consecutive_failures=prior.consecutive_failures if prior is not None else 0,
    )
    if not await state_store.record_attempt(attempt):
        return IngestOutcome(IngestStop.SKIPPED)
    await _touch_market_data_heartbeat(heartbeat_store, now_factory)
    context = _WalkContext(
        service=service,
        dataset_store=dataset_store,
        state_store=state_store,
        provider=provider,
        product_id=product_id,
        timeframe=timeframe,
        closed_end=ends_at,
        attempt=attempt,
        retry_at=_next_retry_at(
            attempt.attempted_at,
            retry_base_seconds,
            prior.consecutive_failures if prior is not None else 0,
            jitter_factory(),
        ),
        page_candles=max_candles_per_request,
        pacer=pacer if pacer is not None else ProviderPacer(),
        budget=_RequestBudget(max_requests),
        heartbeat_store=heartbeat_store,
        now_factory=now_factory,
    )
    outcome = await _walk(
        context,
        maintenance_kind=maintenance_kind,
        lookback_start=starts_at,
        prior=prior,
        verify_island=verify_current_dataset,
    )
    _logger.info(
        "market_data_ingestion_walk product_id=%s timeframe=%s kind=%s stop=%s requests=%d",
        product_id,
        timeframe.value,
        maintenance_kind.value,
        outcome.stop.value,
        outcome.requests,
    )
    return outcome


async def run_market_data_worker(
    stop_requested: asyncio.Event,
    *,
    service: HistoricalRangeService,
    dataset_store: DatasetStore,
    state_store: MarketDataWorkerStateStore,
    provider: str,
    product_id: str,
    lookback_hours: int,
    interval_seconds: int,
    now_factory: Callable[[], datetime] = lambda: datetime.now(UTC),
    on_readiness_changed: Callable[[bool], None] | None = None,
    watchlist: MarketDataWatchlistStore | None = None,
    timeframe: CandleInterval = CandleInterval.ONE_HOUR,
    heartbeat_store: WorkerHeartbeatStore | None = None,
    settings_store: SettingsStore | None = None,
    retention: DatasetRetentionRunner | None = None,
    request_pause_seconds: float = PROVIDER_REQUEST_PAUSE_SECONDS,
) -> None:
    """Run scheduled ingestion until a supervisor requests graceful shutdown.

    One ``ProviderPacer`` spaces every provider request by ``request_pause_seconds`` and
    makes all targets wait out a rate-limit cooldown together. When ``retention`` is
    attached, a bounded superseded-revision pass runs between ingest cycles (this loop
    is the dataset volume's only writer).
    """
    if on_readiness_changed is not None:
        on_readiness_changed(True)
    verified_targets: set[tuple[str, CandleInterval]] = set()
    proven_floors: set[tuple[str, CandleInterval]] = set()
    pacer = ProviderPacer(pause_seconds=request_pause_seconds, stop_requested=stop_requested)
    try:
        while not stop_requested.is_set():
            cycle_now = now_factory()
            if heartbeat_store is not None:
                await heartbeat_store.touch("market_data_worker", cycle_now.astimezone(UTC))
            cycle_product, cycle_lookback, cycle_interval = _reloadable_ingest_knobs(
                product_id=product_id,
                lookback_hours=lookback_hours,
                interval_seconds=interval_seconds,
                settings_store=settings_store,
            )
            targets = await _cycle_targets(
                watchlist,
                provider=provider,
                product_id=cycle_product,
                timeframe=timeframe,
                lookback_hours=cycle_lookback,
                now=cycle_now,
            )
            wait_seconds = await _ingest_due_targets(
                targets,
                service=service,
                dataset_store=dataset_store,
                state_store=state_store,
                interval_seconds=cycle_interval,
                cycle_now=cycle_now,
                verified_targets=verified_targets,
                stop_requested=stop_requested,
                watchlist=watchlist,
                heartbeat_store=heartbeat_store,
                now_factory=now_factory,
                pacer=pacer,
                proven_floors=proven_floors,
            )
            if retention is not None and not stop_requested.is_set():
                await retention.maybe_run(now_factory())
            if stop_requested.is_set() or wait_seconds is None:
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_requested.wait(), timeout=wait_seconds)
    finally:
        if on_readiness_changed is not None:
            on_readiness_changed(False)


def _reloadable_ingest_knobs(
    *,
    product_id: str,
    lookback_hours: int,
    interval_seconds: int,
    settings_store: SettingsStore | None,
) -> tuple[str, int, int]:
    """Return product/lookback/interval, re-reading YAML when a store is attached."""
    if settings_store is None:
        return product_id, lookback_hours, interval_seconds
    settings = settings_store.current()
    return (
        settings.market_data_worker_product_id,
        settings.market_data_worker_lookback_hours,
        settings.market_data_worker_interval_seconds,
    )


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


def bounded_lookback_start(
    ends_at: datetime,
    lookback_hours: int,
    interval: CandleInterval,
    *,
    max_intervals: int = MAX_HISTORICAL_INTERVAL_COUNT,
) -> datetime:
    """Align a lookback window to the interval without exceeding the range cap."""
    requested = timedelta(hours=lookback_hours)
    max_span = interval.duration * max_intervals
    span = requested if requested <= max_span else max_span
    starts_at = _safe_shift(
        ends_at,
        -span,
        "Market-data worker cannot represent its initial range start.",
    )
    remainder = (ends_at - starts_at) % interval.duration
    if remainder != timedelta(0):
        starts_at = _safe_shift(
            starts_at,
            remainder,
            "Market-data worker cannot represent its aligned range start.",
        )
    if starts_at >= ends_at:
        raise MarketDataWorkerError("Market-data worker lookback collapsed to an empty range.")
    return starts_at


def watch_lookback_start(
    closed_end: datetime,
    lookback_hours: int,
    interval: CandleInterval,
    covered_starts_at: datetime | None,
    history_floor_at: datetime | None,
) -> datetime:
    """Return the oldest bar the watch still needs, clamped to a confirmed provider floor."""
    lookback_start = bounded_lookback_start(closed_end, lookback_hours, interval)
    if (
        history_floor_at is not None
        and covered_starts_at is not None
        and history_floor_at == covered_starts_at
        and lookback_start < history_floor_at
    ):
        return history_floor_at
    return lookback_start


def watch_expected_candle_count(
    lookback_hours: int, interval: CandleInterval, ends_at: datetime
) -> int:
    """Count bars in the watch lookback window ending at ``ends_at``."""
    start = bounded_lookback_start(ends_at, lookback_hours, interval)
    return int((ends_at - start) / interval.duration)


def watch_covered_candle_count(
    covered_starts_at: datetime | None,
    covered_ends_at: datetime | None,
    lookback_hours: int,
    interval: CandleInterval,
    ends_at: datetime,
) -> int:
    """Count the watch lookback window's bars that verified coverage spans (the X of X of Y).

    No-trade bars count as covered; bars before a listing floor do not, so a young
    market reports its real share of the lookback.
    """
    if covered_starts_at is None or covered_ends_at is None:
        return 0
    start = max(covered_starts_at, bounded_lookback_start(ends_at, lookback_hours, interval))
    end = min(covered_ends_at, ends_at)
    if end <= start:
        return 0
    return int((end - start) / interval.duration)


def island_covers_watch(
    *,
    covered_starts_at: datetime | None,
    covered_ends_at: datetime | None,
    island_complete: bool,
    lookback_hours: int,
    interval: CandleInterval,
    closed_end: datetime,
    product_id: str = "",
    now: datetime | None = None,
    history_floor_at: datetime | None = None,
) -> bool:
    """True when the latest complete island spans the full watch lookback.

    When candle freshness is still ``fresh`` and coverage is only one closed bar
    behind ``closed_end``, treat the watch as complete so large grids do not flip
    on every boundary while the single worker is elsewhere. Coverage that reaches
    the settle cutoff also spans the watch: a missing bar inside the settle window
    may still be published, so a sparse market's quiet head waits there without
    being history the watch lacks (ADR 0095).

    A ``history_floor_at`` equal to the island start means the listing search found
    no provider candle before the island (the market had not traded yet), so the
    island satisfies the lookback from that floor.
    """
    if not island_complete or covered_starts_at is None or covered_ends_at is None:
        return False
    lookback_start = watch_lookback_start(
        closed_end, lookback_hours, interval, covered_starts_at, history_floor_at
    )
    if covered_starts_at > lookback_start:
        return False
    if covered_ends_at >= closed_end or covered_ends_at >= settle_cutoff(closed_end, interval):
        return True
    if now is not None and product_id:
        freshness = evaluate_freshness(
            product_id=product_id,
            newest_candle_at=covered_ends_at,
            now=now,
            interval=interval,
        )
        if (
            freshness.status is FreshnessStatus.FRESH
            and covered_ends_at + interval.duration >= closed_end
        ):
            return True
    return False


async def _touch_market_data_heartbeat(
    heartbeat_store: WorkerHeartbeatStore | None,
    now_factory: Callable[[], datetime] | None,
) -> None:
    """Record liveness with wall-clock time so a long cell cannot stale health."""
    if heartbeat_store is None:
        return
    instant = now_factory() if now_factory is not None else datetime.now(UTC)
    await heartbeat_store.touch("market_data_worker", instant.astimezone(UTC))


async def _reconcile_current_coverage(
    *,
    service: HistoricalRangeService,
    dataset_store: DatasetStore,
    state_store: MarketDataWorkerStateStore,
    provider: str,
    product_id: str,
    timeframe: CandleInterval,
    prior: MarketDataWorkerState | None,
    ends_at: datetime,
    lookback_hours: int,
    now: datetime,
    retry_base_seconds: int,
    jitter_factory: Callable[[], float],
    verify_current_dataset: bool,
) -> bool:
    """Return True when coverage is already current and no fetch is required."""
    del service
    if prior is None or not prior.complete or prior.covered_ends_at is None:
        return False
    lookback_start = watch_lookback_start(
        ends_at, lookback_hours, timeframe, prior.covered_starts_at, prior.history_floor_at
    )
    covered_start = prior.covered_starts_at or prior.covered_ends_at
    if lookback_start < covered_start:
        return False
    if prior.covered_ends_at < ends_at:
        return False
    reconciliation_attempt = MarketDataWorkerAttempt(
        provider=provider,
        product_id=product_id,
        timeframe=timeframe,
        attempted_at=now.astimezone(UTC),
        requested_starts_at=prior.covered_starts_at or prior.covered_ends_at,
        requested_ends_at=prior.covered_ends_at,
        maintenance_kind=MarketDataMaintenanceKind.INCREMENTAL,
        expected_ends_at=ends_at,
        next_attempt_at=_safe_shift(
            now.astimezone(UTC),
            timedelta(seconds=retry_base_seconds),
            "Market-data worker cannot represent its next attempt time.",
        ),
        expected_consecutive_failures=prior.consecutive_failures,
    )
    if not await state_store.record_attempt(reconciliation_attempt):
        return True
    if _can_skip_dataset_verify(prior, verify_current_dataset):
        await state_store.record_success(
            MarketDataWorkerSuccess(
                attempt=reconciliation_attempt,
                covered_starts_at=prior.covered_starts_at or prior.covered_ends_at,
                covered_ends_at=prior.covered_ends_at,
                expected_candle_count=prior.expected_candle_count or 0,
                received_candle_count=prior.received_candle_count or 0,
                gap_count=prior.gap_count or 0,
                missing_intervals=prior.missing_intervals or 0,
                content_fingerprint=prior.content_fingerprint or "",
                advances_revision=False,
            )
        )
        _logger.info("market_data_ingestion_current")
        return True
    try:
        verified_candles = dataset_store.load_candles(prior.content_fingerprint or "")
    except Exception:  # noqa: BLE001 - restart reconciliation must fail closed.
        retry_at = _next_retry_at(
            reconciliation_attempt.attempted_at,
            retry_base_seconds,
            prior.consecutive_failures,
            jitter_factory(),
        )
        await _record_failure(
            state_store,
            reconciliation_attempt,
            code="dataset_verification_failed",
            message="The current market-data dataset could not be verified.",
            next_retry_at=retry_at,
        )
        _logger.warning("market_data_ingestion_failed code=dataset_verification_failed")
        return True
    await state_store.record_success(
        MarketDataWorkerSuccess(
            attempt=reconciliation_attempt,
            covered_starts_at=verified_candles[0].starts_at,
            covered_ends_at=_safe_shift(
                verified_candles[-1].starts_at,
                timeframe.duration,
                "Market-data worker cannot represent verified candle coverage.",
            ),
            expected_candle_count=len(verified_candles),
            received_candle_count=len(verified_candles),
            gap_count=0,
            missing_intervals=0,
            content_fingerprint=prior.content_fingerprint or "",
            advances_revision=False,
        )
    )
    _logger.info("market_data_ingestion_current")
    return True


def _can_skip_dataset_verify(prior: MarketDataWorkerState, verify_current_dataset: bool) -> bool:
    """Reuse recorded coverage facts after the first verified cycle of a process."""
    return (
        not verify_current_dataset
        and prior.failure_code != "dataset_verification_failed"
        and prior.covered_starts_at is not None
        and prior.expected_candle_count is not None
        and prior.received_candle_count is not None
        and prior.gap_count is not None
        and prior.missing_intervals is not None
        and prior.content_fingerprint is not None
    )


def _plan_range(
    prior: MarketDataWorkerState | None,
    ends_at: datetime,
    lookback_hours: int,
    timeframe: CandleInterval,
) -> tuple[datetime, MarketDataMaintenanceKind]:
    """Choose initial backfill, prefix backfill, or one-bar overlap incremental extension.

    A confirmed provider-history floor at the island start ends prefix backfill, so
    the island extends forward instead of retrying the same incomplete prefix.
    """
    lookback_start = bounded_lookback_start(ends_at, lookback_hours, timeframe)
    if prior is not None and prior.complete and prior.covered_ends_at is not None:
        lookback_start = watch_lookback_start(
            ends_at, lookback_hours, timeframe, prior.covered_starts_at, prior.history_floor_at
        )
        covered_start = prior.covered_starts_at
        if covered_start is not None and lookback_start < covered_start:
            return lookback_start, MarketDataMaintenanceKind.PREFIX_BACKFILL
        starts_at = _safe_shift(
            prior.covered_ends_at,
            -timeframe.duration,
            "Market-data worker cannot represent its incremental range start.",
        )
        return starts_at, MarketDataMaintenanceKind.INCREMENTAL
    return lookback_start, MarketDataMaintenanceKind.INITIAL_BACKFILL


@dataclass(frozen=True, slots=True)
class _Island:
    """The published complete island a walk extends, read from verified worker state."""

    state: MarketDataWorkerState
    fingerprint: str
    starts_at: datetime
    ends_at: datetime


def _island_from(prior: MarketDataWorkerState | None) -> _Island | None:
    """Return the prior complete island, or None when state lacks full coverage evidence."""
    if (
        prior is None
        or not prior.complete
        or prior.content_fingerprint is None
        or prior.covered_starts_at is None
        or prior.covered_ends_at is None
    ):
        return None
    return _Island(
        state=prior,
        fingerprint=prior.content_fingerprint,
        starts_at=prior.covered_starts_at,
        ends_at=prior.covered_ends_at,
    )


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


def listing_horizon_start(closed_end: datetime, interval: CandleInterval) -> datetime:
    """Return the oldest instant a listing search covers: one daily page past the ceiling.

    The ceiling is the longest lookback a watch on the timeframe may request (ADR 0085), so
    a floor proven back to here holds for every lookback and raising one never needs it
    proven again. The extra 350 days let a search below a quiet lookback start find the
    trade that prices it, so a quiet first bar is never mistaken for a listing.
    """
    ceiling = bounded_lookback_start(closed_end, max_watch_lookback_hours(interval), interval)
    return _utc_day_floor(ceiling) - _LISTING_SEARCH_MARGIN


def _utc_day_floor(value: datetime) -> datetime:
    """Return the UTC midnight at or before one aware UTC instant."""
    return value.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def _walk(
    context: _WalkContext,
    *,
    maintenance_kind: MarketDataMaintenanceKind,
    lookback_start: datetime,
    prior: MarketDataWorkerState | None,
    verify_island: bool,
) -> IngestOutcome:
    """Dispatch the claimed attempt to the newest-first or forward page walk."""
    if maintenance_kind is MarketDataMaintenanceKind.INITIAL_BACKFILL:
        backward = _BackwardWalk(context, lookback_start, None)
        return await _finish_backward(context, backward, None, await backward.run())
    island = _island_from(prior)
    if island is None:
        await _record_failure(
            context.state_store,
            context.attempt,
            code="incomplete_range",
            message="Historical market-data range was incomplete or inconsistent.",
            next_retry_at=context.retry_at,
        )
        return context.outcome(IngestStop.FAILED)
    if verify_island and not await _island_verifies(context, island):
        return context.outcome(IngestStop.FAILED)
    if maintenance_kind is MarketDataMaintenanceKind.PREFIX_BACKFILL:
        backward = _BackwardWalk(context, lookback_start, island)
        return await _finish_backward(context, backward, island, await backward.run())
    if island.ends_at >= context.closed_end:
        return await _record_without_publication(context, island, None, IngestStop.CURRENT)
    forward = _ForwardWalk(context, island)
    return await _finish_forward(context, forward, island, await forward.run())


async def _island_verifies(context: _WalkContext, island: _Island) -> bool:
    """Deep-verify the island once per process before extending it; record failure if not."""
    try:
        context.dataset_store.load_manifest(island.fingerprint)
    except Exception:  # noqa: BLE001 - an unreadable island must fail closed before fetching.
        await _record_dataset_unverifiable(context)
        return False
    return True


async def _island_edge(context: _WalkContext, island: _Island, *, newest: bool) -> Candle | None:
    """Load the island's stored first or last bar; record a failure when it is unreadable.

    A walk needs it when the provider omits the overlap bar because that bar is itself a
    no-trade bar: the extension then starts from the stored bar instead of inventing one.
    """
    try:
        return context.dataset_store.load_edge_candle(island.fingerprint, newest=newest)
    except Exception:  # noqa: BLE001 - an unreadable island must fail closed before publishing.
        await _record_dataset_unverifiable(context)
        return None


async def _record_dataset_unverifiable(context: _WalkContext) -> None:
    """Record the redacted failure for an island that could not be verified or read."""
    await _record_failure(
        context.state_store,
        context.attempt,
        code="dataset_verification_failed",
        message="The current market-data dataset could not be verified.",
        next_retry_at=context.retry_at,
    )
    _logger.warning("market_data_ingestion_failed code=dataset_verification_failed")


type _Direction = Literal["forward", "prefix", "initial", "listing_probe"]


async def _request(
    context: _WalkContext,
    starts_at: datetime,
    ends_at: datetime,
    timeframe: CandleInterval,
) -> CandleRangeReport | IngestStop:
    """Spend one paced provider request; map throttles and failures to a walk stop."""
    if not await context.pacer.acquire():
        return IngestStop.STOPPED
    context.budget.spent += 1
    await _touch_market_data_heartbeat(context.heartbeat_store, context.now_factory)
    try:
        report = await fetch_historical_range(
            context.service,
            context.product_id,
            timeframe,
            starts_at,
            ends_at,
            context.closed_end,
        )
    except MarketDataRateLimitedError:
        cooldown = context.pacer.throttled_by_provider()
        _logger.warning(
            "market_data_ingestion_rate_limited product_id=%s timeframe=%s cooldown_seconds=%.1f",
            context.product_id,
            timeframe.value,
            cooldown,
        )
        return IngestStop.RATE_LIMITED
    except Exception as error:  # noqa: BLE001 - provider boundary is intentionally fail-closed.
        context.pacer.completed()
        _log_provider_unavailable(context.product_id, timeframe, starts_at, ends_at, error)
        return IngestStop.FAILED
    context.pacer.completed()
    return report


async def _fetch_page(
    context: _WalkContext,
    starts_at: datetime,
    ends_at: datetime,
    direction: _Direction,
    *,
    timeframe: CandleInterval | None = None,
    cutoff: datetime | None = None,
) -> CandlePage | IngestStop:
    """Fetch one page, re-fetching once before acting on a settled or inconsistent gap.

    A bar counts as missing only when both responses omit it, so a transient short page
    never becomes a no-trade bar or a listing floor. A missing bar at or after ``cutoff``
    (default: the walk's settle window) is not confirmed; the walk waits for it.
    ``timeframe`` lets the listing search request daily candles.
    """
    interval = context.timeframe if timeframe is None else timeframe
    settle = context.cutoff if cutoff is None else cutoff
    report = await _request(context, starts_at, ends_at, interval)
    if isinstance(report, IngestStop):
        return report
    page = split_page(report, starts_at, ends_at, interval)
    if not page.needs_confirmation(settle):
        return page
    confirmed = await _request(context, starts_at, ends_at, interval)
    if isinstance(confirmed, IngestStop):
        return confirmed
    merged = merge_confirmed_pages(page, split_page(confirmed, starts_at, ends_at, interval))
    if not merged.complete:
        _log_chunk_incomplete(
            context.product_id, interval, starts_at, ends_at, confirmed, direction
        )
    return merged


class _BackwardWalk:
    """Newest-first page walk that assembles one gap-filled segment toward the lookback start.

    Confirmed no-trade intervals between real candles (and, for initial backfill, after
    the newest real candle up to the first unsettled bar) become flat bars (ADR 0095).
    The walk is anchored when a real candle at or before the lookback start begins the
    segment; when the lookback start itself had no trades, the newest candle below it
    prices flat bars from the lookback start instead. A confirmed page with no candle at
    all starts a listing search: pages to the UTC day boundary, then daily-candle probes
    that skip whole days without trades. Only a search that reaches the listing horizon
    without any candle records ``history_floor_at``: the market had not traded yet.
    Prefix backfill starts one overlap bar past the island start; when the provider omits
    that bar (a stored no-trade bar), the segment ends at the stored island head instead.
    """

    def __init__(
        self, context: _WalkContext, lookback_start: datetime, island: _Island | None
    ) -> None:
        """Start at the newest closed bar, or one overlap bar past the island start."""
        self._context = context
        self._target = lookback_start
        self._horizon = listing_horizon_start(context.closed_end, context.timeframe)
        self._island = island
        self._pages: list[tuple[Candle, ...]] = []
        self._cursor = (
            context.closed_end
            if island is None
            else _safe_shift(
                island.starts_at,
                context.bar,
                "Market-data worker cannot represent a prefix overlap end.",
            )
        )
        self._segment_end: datetime | None = None if island is None else self._cursor
        self._anchor_close: Decimal | None = None
        self._searching = False
        self._probed_at: datetime | None = None
        self.floor: datetime | None = None
        self.needs_head = False

    async def run(self) -> IngestStop:
        """Fetch pages until the segment is anchored, the listing floor, the budget, or a stop."""
        while not self._anchored():
            if self._cursor <= self._horizon:
                return self._listing_floor()
            if self._cursor <= self._target and self._oldest_start() is None:
                # No trade anywhere in the lookback: nothing to anchor or publish.
                return IngestStop.COMPLETE
            if self._out_of_budget():
                return IngestStop.BUDGET
            if self._should_probe_days():
                stop = await self._skip_days_without_trades()
            else:
                stop = await self._fetch_and_absorb()
            if stop is not None:
                return stop
        return IngestStop.COMPLETE

    def segment(self, head: Candle | None) -> tuple[Candle, ...]:
        """Return the gap-filled segment, oldest first; empty when nothing real was found."""
        real = [candle for page in reversed(self._pages) for candle in page]
        if head is not None:
            real.append(head)
        filled = fill_no_trade_gaps(real, self._context.timeframe, through=self._segment_end)
        if not filled or self._anchor_close is None or filled[0].starts_at <= self._target:
            return filled
        leading: list[Candle] = []
        cursor = self._target
        while cursor < filled[0].starts_at:
            leading.append(no_trade_bar(cursor, self._anchor_close))
            cursor = cursor + self._context.bar
        return (*leading, *filled)

    def _oldest_start(self) -> datetime | None:
        """Return the oldest real bar the segment or its island covers."""
        if self._pages:
            return self._pages[-1][0].starts_at
        if self._island is not None:
            return self._island.starts_at
        return None

    def _anchored(self) -> bool:
        """True once the segment reaches back to the lookback start through real prices."""
        if self._anchor_close is not None:
            return True
        oldest = self._oldest_start()
        return oldest is not None and oldest <= self._target

    def _listing_floor(self) -> IngestStop:
        """End a listing search that found no provider candle before the segment."""
        self.floor = self._oldest_start()
        return IngestStop.LISTING_FLOOR

    def _out_of_budget(self) -> bool:
        """True when the walk must stop; a listing search may use a bounded allowance."""
        if self._searching:
            return self._context.budget.exhausted(allowance=LISTING_SEARCH_REQUEST_ALLOWANCE)
        return self._context.budget.exhausted()

    def _should_probe_days(self) -> bool:
        """True when a listing search sits on a UTC day boundary it has not probed yet."""
        return (
            self._searching
            and self._context.can_probe_days
            and self._cursor == _utc_day_floor(self._cursor)
            and self._probed_at != self._cursor
        )

    def _page_start(self) -> datetime:
        """Return the next page start: clipped at the lookback start, then the horizon.

        A listing search does not page across a UTC day boundary, so it reaches one and
        can probe the whole days below it with daily candles.
        """
        bound = self._target if self._cursor > self._target else self._horizon
        earliest = _safe_shift(
            self._cursor,
            -self._context.page_span,
            "Market-data worker cannot represent a page start.",
        )
        start = max(bound, earliest)
        if self._searching and self._context.can_probe_days:
            start = max(start, _utc_day_floor(self._cursor - self._context.bar))
        return start

    async def _fetch_and_absorb(self) -> IngestStop | None:
        """Fetch the next older page and fold its confirmed candles into the segment."""
        direction: _Direction = "initial" if self._island is None else "prefix"
        page = await _fetch_page(self._context, self._page_start(), self._cursor, direction)
        if isinstance(page, IngestStop):
            return page
        return self._absorb(page)

    def _absorb(self, page: CandlePage) -> IngestStop | None:
        """Keep the page's usable candles; return a stop when the segment cannot grow."""
        if not page.consistent:
            return IngestStop.INCONSISTENT
        unsettled = page.first_unsettled_missing(self._context.cutoff)
        if self._segment_end is None:
            # Initial backfill: the segment ends before the first bar still settling.
            self._segment_end = page.ends_at if unsettled is None else unsettled
        elif unsettled is not None:
            return IngestStop.UNSETTLED
        usable = tuple(candle for candle in page.candles() if candle.starts_at < self._segment_end)
        if self._island is not None and page.ends_at == self._segment_end:
            self.needs_head = not usable or usable[-1].starts_at != self._island.starts_at
        previous_oldest = self._oldest_start()
        # A page that adds no candle older than the segment (only the overlap bar, or
        # nothing) leaves a confirmed-empty span below it: the listing search continues.
        self._searching = not usable or (
            previous_oldest is not None and usable[0].starts_at >= previous_oldest
        )
        self._cursor = page.starts_at
        if not usable:
            return None
        if page.ends_at <= self._target:
            # Below the lookback start: the newest trade prices the bars from that start.
            self._anchor_close = usable[-1].close
            return None
        self._pages.append(usable)
        return None

    async def _skip_days_without_trades(self) -> IngestStop | None:
        """Move the search cursor past whole UTC days that have no daily candle.

        Probes newest first with confirmed daily-granularity pages. The cursor lands at the
        end of the newest day that traded, or at the horizon when no day before it did.
        """
        self._probed_at = self._cursor
        lower = _utc_day_floor(self._horizon)
        span = CandleInterval.ONE_DAY.duration * self._context.page_candles
        day_end = self._cursor
        while day_end > lower:
            start = max(lower, day_end - span)
            page = await _fetch_page(
                self._context,
                start,
                day_end,
                "listing_probe",
                timeframe=CandleInterval.ONE_DAY,
                cutoff=day_end,
            )
            if isinstance(page, IngestStop):
                return page
            if not page.consistent:
                return IngestStop.INCONSISTENT
            traded = page.candles()
            if traded:
                self._cursor = traded[-1].starts_at + CandleInterval.ONE_DAY.duration
                self._probed_at = self._cursor
                return None
            day_end = start
        self._cursor = self._horizon
        return None


class _ForwardWalk:
    """Oldest-first page walk from the island's overlap bar toward the newest closed bar.

    Every page extends the island: the walk never starts a detached island and never
    records or moves ``history_floor_at``. Confirmed no-trade intervals become flat bars at
    the previous close (ADR 0095). A missing bar inside the settle window ends the walk
    until a later cycle, so nothing after it is published yet and a late candle is never
    replaced. When the provider omits the overlap bar (a stored no-trade bar), the
    extension starts from the stored island tail.
    """

    def __init__(self, context: _WalkContext, island: _Island) -> None:
        """Start at the island's last bar so the first page overlaps it."""
        self._context = context
        self._island = island
        self._overlap = _safe_shift(
            island.ends_at,
            -context.timeframe.duration,
            "Market-data worker cannot represent its incremental range start.",
        )
        self._cursor = self._overlap
        self._real: list[Candle] = []
        self._end = island.ends_at

    async def run(self) -> IngestStop:
        """Fetch pages until the newest closed bar, an unsettled bar, the budget, or a stop."""
        while self._cursor < self._context.closed_end:
            if self._context.budget.exhausted():
                return IngestStop.BUDGET
            latest = _safe_shift(
                self._cursor,
                self._context.page_span,
                "Market-data worker cannot represent a page end.",
            )
            page_end = min(self._context.closed_end, latest)
            page = await _fetch_page(self._context, self._cursor, page_end, "forward")
            if isinstance(page, IngestStop):
                return page
            if not page.consistent:
                return IngestStop.INCONSISTENT
            unsettled = page.first_unsettled_missing(self._context.cutoff)
            boundary = page.ends_at if unsettled is None else unsettled
            self._real.extend(candle for candle in page.candles() if candle.starts_at < boundary)
            self._end = max(self._end, boundary)
            if unsettled is not None:
                return IngestStop.UNSETTLED
            self._cursor = page_end
        return IngestStop.COMPLETE

    @property
    def needs_tail(self) -> bool:
        """True when the extension must start from the stored island tail bar."""
        return self._end > self._island.ends_at and (
            not self._real or self._real[0].starts_at != self._overlap
        )

    def extension(self, tail: Candle | None) -> tuple[Candle, ...]:
        """Return the overlap bar onward, gap-filled through the publishable end."""
        if self._end <= self._island.ends_at:
            return ()
        real = list(self._real)
        if tail is not None and (not real or real[0].starts_at != self._overlap):
            real.insert(0, tail)
        return fill_no_trade_gaps(real, self._context.timeframe, through=self._end)


async def _finish_backward(
    context: _WalkContext,
    walk: _BackwardWalk,
    island: _Island | None,
    stop: IngestStop,
) -> IngestOutcome:
    """Publish the walked segment as a new island or a prefix revision, then record state."""
    head: Candle | None = None
    if walk.needs_head and island is not None:
        head = await _island_edge(context, island, newest=False)
        if head is None:
            return context.outcome(IngestStop.FAILED)
    candles = walk.segment(head)
    if candles and (island is None or candles[0].starts_at < island.starts_at):
        return await _publish_and_record(
            context,
            candles,
            extend_fingerprint=None if island is None else island.fingerprint,
            history_floor_at=walk.floor,
            stop=stop,
        )
    return await _record_without_publication(context, island, walk.floor, stop)


async def _finish_forward(
    context: _WalkContext,
    walk: _ForwardWalk,
    island: _Island,
    stop: IngestStop,
) -> IngestOutcome:
    """Publish the island's forward extension, then record state; floors never move here."""
    tail: Candle | None = None
    if walk.needs_tail:
        tail = await _island_edge(context, island, newest=True)
        if tail is None:
            return context.outcome(IngestStop.FAILED)
    extension = walk.extension(tail)
    if extension and run_end(extension, context.timeframe) > island.ends_at:
        return await _publish_and_record(
            context,
            extension,
            extend_fingerprint=island.fingerprint,
            history_floor_at=None,
            stop=stop,
        )
    return await _record_without_publication(context, island, None, stop)


async def _publish_and_record(
    context: _WalkContext,
    candles: Sequence[Candle],
    *,
    extend_fingerprint: str | None,
    history_floor_at: datetime | None,
    stop: IngestStop,
) -> IngestOutcome:
    """Write or extend one complete segment, read it back, and record the verified island."""
    report = _segment_report(candles, context.timeframe)
    verified = (
        None
        if report is None
        else _persist_complete_range(
            dataset_store=context.dataset_store,
            provider=context.provider,
            product_id=context.product_id,
            extend_fingerprint=extend_fingerprint,
            report=report,
        )
    )
    if verified is None:
        await _record_failure(
            context.state_store,
            context.attempt,
            code="dataset_persistence_failed",
            message="Validated market-data publication failed.",
            next_retry_at=context.retry_at,
        )
        return context.outcome(IngestStop.FAILED)
    floor = (
        history_floor_at
        if history_floor_at is not None
        and history_floor_at == _parse_manifest_instant(verified.starts_at)
        else None
    )
    synthetic = count_no_trade_bars(candles)
    if synthetic:
        _logger.info(
            "market_data_no_trade_bars_published product_id=%s timeframe=%s "
            "no_trade_bars=%d segment_bars=%d",
            context.product_id,
            context.timeframe.value,
            synthetic,
            len(candles),
        )
    await _record_island_success(
        context.state_store, context.attempt, verified, history_floor_at=floor
    )
    return context.outcome(stop)


def _segment_report(
    candles: Sequence[Candle], interval: CandleInterval
) -> CandleRangeReport | None:
    """Re-analyze one assembled segment as an exact half-open range; None if not complete."""
    starts_at = candles[0].starts_at
    ends_at = run_end(candles, interval)
    report = analyze_range(tuple(candles), interval, starts_at, ends_at, ends_at)
    if not report.complete:
        _logger.warning("market_data_ingestion_failed code=segment_incomplete")
        return None
    return report


async def _record_without_publication(
    context: _WalkContext,
    island: _Island | None,
    history_floor_at: datetime | None,
    stop: IngestStop,
) -> IngestOutcome:
    """Record a walk that published nothing: a failure, unchanged coverage, or shutdown."""
    if stop is IngestStop.STOPPED:
        return context.outcome(stop)
    failure = _failure_for(stop, island)
    if failure is not None:
        code, message, retry_at = failure
        await _record_failure(
            context.state_store,
            context.attempt,
            code=code,
            message=message,
            next_retry_at=context.retry_at if retry_at is None else retry_at(context),
        )
        return context.outcome(IngestStop.FAILED if stop is IngestStop.COMPLETE else stop)
    if island is not None:
        await _record_unchanged_island(context, island, history_floor_at)
    return context.outcome(stop)


type _FailureSpec = tuple[str, str, Callable[[_WalkContext], datetime] | None]


def _failure_for(stop: IngestStop, island: _Island | None) -> _FailureSpec | None:
    """Return the redacted failure a no-publication stop records, or None for success."""
    if stop is IngestStop.RATE_LIMITED:
        return (
            "provider_rate_limited",
            "Historical market-data provider rate-limited the worker; it is backing off.",
            _rate_limit_retry_at,
        )
    if stop is IngestStop.FAILED:
        return ("provider_unavailable", "Historical market-data retrieval failed.", None)
    if stop is IngestStop.INCONSISTENT or island is None:
        _logger.warning("market_data_ingestion_failed code=incomplete_range")
        return (
            "incomplete_range",
            "Historical market-data range was incomplete or inconsistent.",
            None,
        )
    return None


def _rate_limit_retry_at(context: _WalkContext) -> datetime:
    """Return the retry instant after a throttle: the pacer's cooldown, at least one second."""
    seconds = max(1.0, context.pacer.cooldown_seconds)
    return _safe_shift(
        context.attempt.attempted_at,
        timedelta(seconds=seconds),
        "Market-data worker cannot represent its rate-limit retry time.",
    )


async def _record_unchanged_island(
    context: _WalkContext, island: _Island, history_floor_at: datetime | None
) -> None:
    """Record success for an island this walk did not change, optionally adding its floor."""
    state = island.state
    await context.state_store.record_success(
        MarketDataWorkerSuccess(
            attempt=context.attempt,
            covered_starts_at=island.starts_at,
            covered_ends_at=island.ends_at,
            expected_candle_count=state.expected_candle_count or 0,
            received_candle_count=state.received_candle_count or 0,
            gap_count=state.gap_count or 0,
            missing_intervals=state.missing_intervals or 0,
            content_fingerprint=island.fingerprint,
            advances_revision=False,
            history_floor_at=history_floor_at if history_floor_at == island.starts_at else None,
        )
    )


def _persist_complete_range(
    *,
    dataset_store: DatasetStore,
    provider: str,
    product_id: str,
    extend_fingerprint: str | None,
    report: CandleRangeReport,
) -> DatasetManifest | None:
    """Write or extend one complete range; return None when publication fails closed."""
    try:
        published = (
            dataset_store.extend(extend_fingerprint, report)
            if extend_fingerprint is not None
            else dataset_store.write(provider, product_id, report)
        )
        return dataset_store.load_verified(published.manifest_path)
    except Exception:  # noqa: BLE001 - persistence boundary is intentionally fail-closed.
        _logger.warning("market_data_ingestion_failed code=dataset_persistence_failed")
        return None


async def _record_island_success(
    state_store: MarketDataWorkerStateStore,
    attempt: MarketDataWorkerAttempt,
    verified: DatasetManifest,
    *,
    history_floor_at: datetime | None = None,
) -> None:
    """Record worker coverage for the newest complete contiguous published island."""
    if history_floor_at is not None:
        _logger.warning(
            "market_data_history_floor_recorded product_id=%s timeframe=%s history_floor_at=%s",
            attempt.product_id,
            attempt.timeframe.value,
            history_floor_at.isoformat(),
        )
    await state_store.record_success(
        MarketDataWorkerSuccess(
            attempt=attempt,
            covered_starts_at=_parse_manifest_instant(verified.starts_at),
            covered_ends_at=_parse_manifest_instant(verified.ends_at),
            expected_candle_count=verified.expected_candle_count,
            received_candle_count=verified.received_candle_count,
            gap_count=verified.gap_count,
            missing_intervals=verified.missing_intervals,
            content_fingerprint=verified.content_fingerprint,
            history_floor_at=history_floor_at,
        )
    )
    _logger.info("market_data_ingestion_succeeded")


def _parse_manifest_instant(value: str) -> datetime:
    """Parse one manifest ISO instant into an aware UTC datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _log_chunk_incomplete(
    product_id: str,
    timeframe: CandleInterval,
    starts_at: datetime,
    ends_at: datetime,
    report: CandleRangeReport,
    direction: _Direction,
) -> None:
    """Warn with the target and exact page bounds; candle values and secrets stay out."""
    _logger.warning(
        "market_data_ingestion_failed code=chunk_incomplete product_id=%s timeframe=%s "
        "direction=%s starts_at=%s ends_at=%s expected=%d received=%d missing_intervals=%d",
        product_id,
        timeframe.value,
        direction,
        starts_at.isoformat(),
        ends_at.isoformat(),
        report.requested_candle_count,
        report.quality.candle_count,
        report.quality.missing_intervals,
    )


def _log_provider_unavailable(
    product_id: str,
    timeframe: CandleInterval,
    starts_at: datetime,
    ends_at: datetime,
    error: Exception,
) -> None:
    """Warn with target, bounds and exception class only; provider messages may carry URLs."""
    _logger.warning(
        "market_data_ingestion_failed code=provider_unavailable product_id=%s timeframe=%s "
        "starts_at=%s ends_at=%s error_type=%s",
        product_id,
        timeframe.value,
        starts_at.isoformat(),
        ends_at.isoformat(),
        type(error).__name__,
    )


async def _cycle_targets(
    watchlist: MarketDataWatchlistStore | None,
    *,
    provider: str,
    product_id: str,
    timeframe: CandleInterval,
    lookback_hours: int,
    now: datetime,
) -> tuple[MarketDataWatchTarget, ...]:
    """Prefer enabled or requested watchlist rows, otherwise the configured default."""
    if watchlist is not None:
        listed = await watchlist.list_all()
        due = tuple(
            target for target in listed if target.enabled or target.ingest_requested_at is not None
        )
        if due:
            return due
    return (
        MarketDataWatchTarget(
            provider=provider,
            product_id=product_id,
            timeframe=timeframe,
            lookback_hours=lookback_hours,
            enabled=True,
            updated_at=now.astimezone(UTC),
        ),
    )


class _TargetPriority(IntEnum):
    """Cycle order: cheap upkeep of covered watches, requested backfill, other backfill."""

    MAINTENANCE = 0
    REQUESTED = 1
    BACKFILL = 2


_NO_REQUEST = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _TargetPlan:
    """One target's scheduling facts for the current cycle."""

    target: MarketDataWatchTarget
    priority: _TargetPriority
    in_backoff: bool
    skip_reconcile: bool
    closed_end: datetime
    position: int = field(default=0)

    @property
    def requested(self) -> bool:
        """True when an operator queued ingest for this target."""
        return self.target.ingest_requested_at is not None

    @property
    def request_budget(self) -> int:
        """Return the provider requests this target may spend this cycle."""
        if self.priority is _TargetPriority.REQUESTED:
            return INGEST_REQUESTS_PER_REQUESTED_TARGET_CYCLE
        return INGEST_REQUESTS_PER_TARGET_CYCLE

    def sort_key(self) -> tuple[int, datetime, int]:
        """Order by priority, then oldest request first, then watchlist order."""
        requested_at = self.target.ingest_requested_at or _NO_REQUEST
        if self.priority is not _TargetPriority.REQUESTED:
            requested_at = _NO_REQUEST
        return (int(self.priority), requested_at, self.position)


async def _plan_targets(
    targets: tuple[MarketDataWatchTarget, ...],
    state_store: MarketDataWorkerStateStore,
    cycle_now: datetime,
) -> tuple[_TargetPlan, ...]:
    """Classify each target from durable state and return them in cycle priority order."""
    plans: list[_TargetPlan] = []
    for position, target in enumerate(targets):
        prior = await _load_validated_state(
            state_store, target.provider, target.product_id, target.timeframe
        )
        closed_end = target.timeframe.align_closed_end(cycle_now)
        covers = island_covers_watch(
            covered_starts_at=None if prior is None else prior.covered_starts_at,
            covered_ends_at=None if prior is None else prior.covered_ends_at,
            island_complete=bool(prior is not None and prior.complete),
            lookback_hours=target.lookback_hours,
            interval=target.timeframe,
            closed_end=closed_end,
            product_id=target.product_id,
            now=cycle_now,
            history_floor_at=None if prior is None else prior.history_floor_at,
        )
        if covers:
            priority = _TargetPriority.MAINTENANCE
        elif target.ingest_requested_at is not None:
            priority = _TargetPriority.REQUESTED
        else:
            priority = _TargetPriority.BACKFILL
        plans.append(
            _TargetPlan(
                target=target,
                priority=priority,
                in_backoff=_in_backoff(prior, cycle_now),
                skip_reconcile=prior is not None and prior.complete and not covers,
                closed_end=closed_end,
                position=position,
            )
        )
    return tuple(sorted(plans, key=_TargetPlan.sort_key))


async def _ingest_due_targets(
    targets: tuple[MarketDataWatchTarget, ...],
    *,
    service: HistoricalRangeService,
    dataset_store: DatasetStore,
    state_store: MarketDataWorkerStateStore,
    interval_seconds: int,
    cycle_now: datetime,
    verified_targets: set[tuple[str, CandleInterval]],
    stop_requested: asyncio.Event,
    watchlist: MarketDataWatchlistStore | None = None,
    heartbeat_store: WorkerHeartbeatStore | None = None,
    now_factory: Callable[[], datetime] | None = None,
    pacer: ProviderPacer | None = None,
    proven_floors: set[tuple[str, CandleInterval]] | None = None,
) -> int | None:
    """Ingest due targets in priority order. None asks the caller to start the next cycle now.

    Every due target spends at most its request budget, so one long backfill cannot
    starve the others. When any target ran out of budget with work left, the worker
    skips its idle wait and starts the next cycle immediately. ``proven_floors`` holds
    the targets whose recorded history floor was proven again in this process.
    """
    shared_pacer = pacer if pacer is not None else ProviderPacer(stop_requested=stop_requested)
    floors_proven = proven_floors if proven_floors is not None else set()
    more_work = False
    for plan in await _plan_targets(targets, state_store, cycle_now):
        if stop_requested.is_set():
            break
        if plan.in_backoff and not plan.requested:
            continue
        await _touch_market_data_heartbeat(heartbeat_store, now_factory)
        outcome = await _ingest_planned_target(
            plan,
            service=service,
            dataset_store=dataset_store,
            state_store=state_store,
            interval_seconds=interval_seconds,
            cycle_now=cycle_now,
            verified_targets=verified_targets,
            proven_floors=floors_proven,
            watchlist=watchlist,
            heartbeat_store=heartbeat_store,
            now_factory=now_factory,
            pacer=shared_pacer,
        )
        more_work = more_work or outcome.more_work
    if more_work and not stop_requested.is_set():
        return None
    return await _next_wait_seconds(state_store, targets, cycle_now, interval_seconds)


async def _ingest_planned_target(
    plan: _TargetPlan,
    *,
    service: HistoricalRangeService,
    dataset_store: DatasetStore,
    state_store: MarketDataWorkerStateStore,
    interval_seconds: int,
    cycle_now: datetime,
    verified_targets: set[tuple[str, CandleInterval]],
    proven_floors: set[tuple[str, CandleInterval]],
    watchlist: MarketDataWatchlistStore | None,
    heartbeat_store: WorkerHeartbeatStore | None,
    now_factory: Callable[[], datetime] | None,
    pacer: ProviderPacer,
) -> IngestOutcome:
    """Run one budgeted ``ingest_once`` and clear a satisfied ingest request.

    A target's recorded history floor is proven again on its first visit per process, so
    a floor written by an older worker (or during a deploy) never pins a series short.
    """
    target = plan.target
    key = (target.product_id, target.timeframe)
    try:
        outcome = await ingest_once(
            service=service,
            dataset_store=dataset_store,
            state_store=state_store,
            provider=target.provider,
            product_id=target.product_id,
            lookback_hours=target.lookback_hours,
            now=cycle_now,
            timeframe=target.timeframe,
            retry_base_seconds=interval_seconds,
            verify_current_dataset=key not in verified_targets,
            skip_reconcile=plan.skip_reconcile,
            heartbeat_store=heartbeat_store,
            now_factory=now_factory,
            max_requests=plan.request_budget,
            pacer=pacer,
            reprove_history_floor=key not in proven_floors,
        )
    finally:
        if plan.requested and watchlist is not None:
            await _clear_satisfied_request(plan, state_store, watchlist, cycle_now)
        verified_targets.add(key)
    if outcome.stop in _FLOOR_PROVEN_STOPS:
        proven_floors.add(key)
    return outcome


async def _clear_satisfied_request(
    plan: _TargetPlan,
    state_store: MarketDataWorkerStateStore,
    watchlist: MarketDataWatchlistStore,
    cycle_now: datetime,
) -> None:
    """Clear ``ingest_requested_at`` once the island spans the watch lookback."""
    target = plan.target
    state_after = await _load_validated_state(
        state_store, target.provider, target.product_id, target.timeframe
    )
    watch_done = state_after is not None and island_covers_watch(
        covered_starts_at=state_after.covered_starts_at,
        covered_ends_at=state_after.covered_ends_at,
        island_complete=state_after.complete,
        lookback_hours=target.lookback_hours,
        interval=target.timeframe,
        closed_end=plan.closed_end,
        product_id=target.product_id,
        now=cycle_now,
        history_floor_at=state_after.history_floor_at,
    )
    if watch_done:
        await watchlist.clear_ingest_request(target.provider, target.product_id, target.timeframe)


def _in_backoff(state: MarketDataWorkerState | None, now: datetime) -> bool:
    """True when a prior failure scheduled a retry after the current instant."""
    return (
        state is not None
        and state.next_retry_at is not None
        and state.next_retry_at > now.astimezone(UTC)
    )


async def _next_wait_seconds(
    state_store: MarketDataWorkerStateStore,
    targets: tuple[MarketDataWatchTarget, ...],
    cycle_now: datetime,
    interval_seconds: int,
) -> int:
    """Wait at least one second, preferring the earliest recorded retry."""
    wait_seconds = interval_seconds
    now = cycle_now.astimezone(UTC)
    for target in targets:
        state = await _load_validated_state(
            state_store, target.provider, target.product_id, target.timeframe
        )
        if state is not None and state.next_retry_at is not None:
            wait_seconds = min(
                wait_seconds,
                max(1, int((state.next_retry_at - now).total_seconds())),
            )
    return max(1, min(wait_seconds, INGEST_REQUEST_POLL_SECONDS))


async def _record_failure(
    state_store: MarketDataWorkerStateStore,
    attempt: MarketDataWorkerAttempt,
    *,
    code: str,
    message: str,
    next_retry_at: datetime,
) -> None:
    """Persist one stable redacted failure outcome."""
    await state_store.record_failure(
        MarketDataWorkerFailure(
            attempt=attempt,
            code=code,
            message=message,
            next_retry_at=next_retry_at,
        )
    )


def _next_retry_at(
    attempted_at: datetime,
    base_seconds: int,
    prior_failures: int,
    jitter_value: float,
) -> datetime:
    """Return a capped exponential retry instant with up to twenty-percent positive jitter."""
    bounded_jitter = min(max(jitter_value, 0.0), 1.0)
    base_delay = min(base_seconds * (2**prior_failures), 3_600)
    delay = base_delay + int(base_delay * 0.2 * bounded_jitter)
    return _safe_shift(
        attempted_at,
        timedelta(seconds=delay),
        "Market-data worker cannot represent its retry schedule.",
    )


def _safe_shift(value: datetime, delta: timedelta, message: str) -> datetime:
    """Shift one worker instant without leaking an unrepresentable datetime boundary."""
    try:
        return value + delta
    except OverflowError as error:
        raise MarketDataWorkerError(message) from error
