"""Core lifecycle for complete-only historical market-data publication.

Ingest walks fetch interval-aligned provider pages of at most
``HISTORICAL_REQUEST_MAX_CANDLES`` bars and publish one cumulative revision per walk
(ADR 0085). Initial backfill starts at the newest closed bar and walks back toward the
lookback start, so coverage is fresh after the first request. A confirmed provider hole
directly before the island becomes ``history_floor_at`` and ends the walk. Incremental
maintenance walks forward from a one-bar overlap. A missing bar always ends a run;
nothing is interpolated.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import IntEnum, StrEnum
import logging
import random
from typing import TYPE_CHECKING, Literal, Protocol

from thytrader.market_data.freshness import FreshnessStatus, evaluate_freshness
from thytrader.market_data.models import (
    HISTORICAL_REQUEST_MAX_CANDLES,
    MAX_HISTORICAL_INTERVAL_COUNT,
    CandleInterval,
    CandleRangeReport,
    MarketDataRateLimitedError,
)
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
from thytrader.market_data_worker.pages import CandlePage, run_end, settle_cutoff, split_page

_logger = logging.getLogger(__name__)

# Fair share of provider requests one target may spend per worker cycle. A target with a
# pending ``ingest_requested_at`` gets the larger share; every due target is visited
# each cycle, so a long backfill never starves maintenance of the others.
INGEST_REQUESTS_PER_TARGET_CYCLE = 8
INGEST_REQUESTS_PER_REQUESTED_TARGET_CYCLE = 24

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from thytrader.market_data.datasets import DatasetManifest, DatasetStore
    from thytrader.market_data.models import Candle
    from thytrader.market_data_worker.pages import CandleRun
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
    """Why one ``ingest_once`` call ended; the cycle scheduler reads it."""

    CURRENT = "current"
    SKIPPED = "skipped"
    COMPLETE = "complete"
    BUDGET = "budget"
    HOLE = "hole"
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


async def _load_validated_state(
    state_store: MarketDataWorkerStateStore,
    provider: str,
    product_id: str,
    timeframe: CandleInterval,
) -> MarketDataWorkerState | None:
    """Load durable state and reject forged timestamps before scheduling."""
    state = await state_store.get(provider, product_id, timeframe)
    return validate_market_data_worker_state(state) if state is not None else None


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
) -> IngestOutcome:
    """Retrieve, verify, and publish complete coverage in provider-sized pages.

    ``max_requests`` bounds provider calls for this call (a confirmation re-fetch counts);
    ``None`` walks until the plan is done. ``max_candles_per_request`` defaults to the
    provider page limit; tests lower it to exercise multi-page walks on small fixtures.
    ``pacer`` spaces requests across targets; ``None`` uses an unpaced one.
    """
    if max_candles_per_request < 2:
        raise ValueError("A provider page must hold an overlap bar plus one new bar.")
    ends_at = timeframe.align_closed_end(now)
    prior = await _load_validated_state(state_store, provider, product_id, timeframe)
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
    on every boundary while the single worker is elsewhere.

    A ``history_floor_at`` equal to the island start means the provider has a
    confirmed hole directly before the island, so the island satisfies the
    lookback from that floor. Earlier bars cannot be published without
    interpolation.
    """
    if not island_complete or covered_starts_at is None or covered_ends_at is None:
        return False
    lookback_start = watch_lookback_start(
        closed_end, lookback_hours, interval, covered_starts_at, history_floor_at
    )
    if covered_starts_at > lookback_start:
        return False
    if covered_ends_at >= closed_end:
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

    def exhausted(self) -> bool:
        """True when no request may start a new page."""
        return self.limit is not None and self.spent >= self.limit


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
    def cutoff(self) -> datetime:
        """Return the instant from which missing bars are not yet confirmed holes."""
        return settle_cutoff(self.closed_end, self.timeframe)

    @property
    def page_span(self) -> timedelta:
        """Return the duration one provider page covers."""
        return self.timeframe.duration * self.page_candles

    def outcome(self, stop: IngestStop) -> IngestOutcome:
        """Return the call outcome with the requests this walk spent."""
        return IngestOutcome(stop, self.budget.spent)


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
        await _record_failure(
            context.state_store,
            context.attempt,
            code="dataset_verification_failed",
            message="The current market-data dataset could not be verified.",
            next_retry_at=context.retry_at,
        )
        _logger.warning("market_data_ingestion_failed code=dataset_verification_failed")
        return False
    return True


async def _request(
    context: _WalkContext, starts_at: datetime, ends_at: datetime
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
            context.timeframe,
            starts_at,
            ends_at,
            context.closed_end,
        )
    except MarketDataRateLimitedError:
        cooldown = context.pacer.throttled_by_provider()
        _logger.warning(
            "market_data_ingestion_rate_limited product_id=%s timeframe=%s cooldown_seconds=%.1f",
            context.product_id,
            context.timeframe.value,
            cooldown,
        )
        return IngestStop.RATE_LIMITED
    except Exception as error:  # noqa: BLE001 - provider boundary is intentionally fail-closed.
        context.pacer.completed()
        _log_provider_unavailable(context.product_id, context.timeframe, starts_at, ends_at, error)
        return IngestStop.FAILED
    context.pacer.completed()
    return report


async def _fetch_page(
    context: _WalkContext,
    starts_at: datetime,
    ends_at: datetime,
    direction: Literal["forward", "prefix", "initial"],
) -> CandlePage | IngestStop:
    """Fetch one page, re-fetching once before acting on a settled or inconsistent hole.

    The confirmation keeps a transient short page from becoming a provider hole. A
    missing bar inside the settle window is not confirmed: the walk waits for it.
    """
    report = await _request(context, starts_at, ends_at)
    if isinstance(report, IngestStop):
        return report
    page = split_page(report, starts_at, ends_at, context.timeframe)
    if not page.needs_confirmation(context.cutoff):
        return page
    confirmed = await _request(context, starts_at, ends_at)
    if isinstance(confirmed, IngestStop):
        return confirmed
    page = split_page(confirmed, starts_at, ends_at, context.timeframe)
    if not page.complete:
        _log_chunk_incomplete(
            context.product_id, context.timeframe, starts_at, ends_at, confirmed, direction
        )
    return page


class _BackwardWalk:
    """Newest-first page walk that grows one gap-free segment toward the lookback start.

    Initial backfill (no island) seeds the segment from the first page's newest usable
    run, so coverage reaches the newest closed bar after one request. Prefix backfill
    starts one bar past the island start, so the segment overlaps the island. Each older
    page must end exactly where the segment starts. A settled missing bar directly
    before the segment ends the walk and becomes the provider-history floor.
    """

    def __init__(
        self, context: _WalkContext, lookback_start: datetime, island: _Island | None
    ) -> None:
        """Start at the newest closed bar, or one overlap bar past the island start."""
        self._context = context
        self._lookback_start = lookback_start
        self._island = island
        self._runs: list[CandleRun] = []
        self.floor: datetime | None = None
        self._cursor_end = (
            context.closed_end
            if island is None
            else _safe_shift(
                island.starts_at,
                context.timeframe.duration,
                "Market-data worker cannot represent a prefix overlap end.",
            )
        )

    async def run(self) -> IngestStop:
        """Fetch pages until the lookback start, a hole, the budget, or a provider stop."""
        direction: Literal["prefix", "initial"] = "initial" if self._island is None else "prefix"
        while self._cursor_end > self._lookback_start:
            if self._context.budget.exhausted():
                return IngestStop.BUDGET
            earliest = _safe_shift(
                self._cursor_end,
                -self._context.page_span,
                "Market-data worker cannot represent a page start.",
            )
            page_start = max(self._lookback_start, earliest)
            page = await _fetch_page(self._context, page_start, self._cursor_end, direction)
            if isinstance(page, IngestStop):
                return page
            stop = self._absorb(page)
            if stop is not None:
                return stop
        return IngestStop.COMPLETE

    def candles(self) -> tuple[Candle, ...]:
        """Return the assembled segment, oldest first."""
        return tuple(candle for run in reversed(self._runs) for candle in run)

    def _absorb(self, page: CandlePage) -> IngestStop | None:
        """Prepend the page's attachable run; return a stop when the segment cannot grow."""
        run = self._attachable_run(page)
        if run is None:
            return self._hole_before(page.ends_at)
        self._runs.append(run)
        if run[0].starts_at > page.starts_at:
            return self._hole_before(run[0].starts_at)
        self._cursor_end = page.starts_at
        return None

    def _attachable_run(self, page: CandlePage) -> CandleRun | None:
        """Pick the newest usable run for initial backfill, otherwise the page's tail run."""
        if self._island is None and not self._runs:
            boundary = page.first_unsettled_missing(self._context.cutoff) or page.ends_at
            return page.newest_run_ending_by(boundary)
        return page.tail_run()

    def _hole_before(self, boundary: datetime) -> IngestStop:
        """End the walk at a hole; a settled hole directly before the segment is its floor."""
        missing_bar = boundary - self._context.timeframe.duration
        segment_start = self._segment_start()
        if segment_start is not None and missing_bar < self._context.cutoff:
            self.floor = segment_start
        return IngestStop.HOLE

    def _segment_start(self) -> datetime | None:
        """Return the oldest bar the segment (or its island) currently covers."""
        if self._runs:
            return self._runs[-1][0].starts_at
        if self._island is not None:
            return self._island.starts_at
        return None


class _ForwardWalk:
    """Oldest-first page walk from the island's overlap bar to the newest closed bar.

    Runs that continue the island extend it. A settled hole closes the chain; the next
    run starts a newer detached island whose floor is that hole (the newest contiguous
    island wins). An unsettled hole near the newest bar stops the walk until a later
    cycle, so a late candle never discards a long island.
    """

    def __init__(self, context: _WalkContext, island: _Island) -> None:
        """Start at the island's last bar so the first page overlaps it."""
        self._context = context
        self._cursor = _safe_shift(
            island.ends_at,
            -context.timeframe.duration,
            "Market-data worker cannot represent its incremental range start.",
        )
        self._attached: list[Candle] = []
        self._detached: list[Candle] = []
        self._chain: Literal["attached", "detached", "closed"] = "attached"

    async def run(self) -> IngestStop:
        """Fetch pages until the newest closed bar, a hole, the budget, or a provider stop."""
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
            stop = self._absorb(page)
            if stop is not None:
                return stop
            self._cursor = page_end
        return IngestStop.COMPLETE

    def attached_candles(self) -> tuple[Candle, ...]:
        """Return the island extension, starting with the overlap bar."""
        return tuple(self._attached)

    def detached_candles(self) -> tuple[Candle, ...]:
        """Return the newest island begun after a settled hole, if any."""
        return tuple(self._detached)

    def _absorb(self, page: CandlePage) -> IngestStop | None:
        """Append the page's runs, closing the chain at settled holes."""
        expected = page.starts_at
        for run in page.runs:
            if run[0].starts_at > expected and self._close_at(expected) is not None:
                return IngestStop.HOLE
            self._append(run)
            expected = run_end(run, self._context.timeframe)
        if expected < page.ends_at and self._close_at(expected) is not None:
            return IngestStop.HOLE
        return None

    def _close_at(self, missing_bar: datetime) -> IngestStop | None:
        """Close the chain at a settled hole; return a stop for an unsettled one."""
        if missing_bar >= self._context.cutoff:
            return IngestStop.HOLE
        self._chain = "closed"
        return None

    def _append(self, run: CandleRun) -> None:
        """Extend the open chain, or start a newer detached island after a hole."""
        if self._chain == "attached":
            self._attached.extend(run)
        elif self._chain == "detached":
            self._detached.extend(run)
        else:
            self._detached = list(run)
            self._chain = "detached"


async def _finish_backward(
    context: _WalkContext,
    walk: _BackwardWalk,
    island: _Island | None,
    stop: IngestStop,
) -> IngestOutcome:
    """Publish the walked segment as a new island or a prefix revision, then record state."""
    candles = walk.candles()
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
    """Publish a newer detached island or the island's forward extension, then record state."""
    detached = walk.detached_candles()
    if detached:
        return await _publish_and_record(
            context,
            detached,
            extend_fingerprint=None,
            history_floor_at=detached[0].starts_at,
            stop=stop,
        )
    attached = walk.attached_candles()
    if attached and run_end(attached, context.timeframe) > island.ends_at:
        return await _publish_and_record(
            context,
            attached,
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
    if island is None:
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
    direction: Literal["forward", "prefix", "initial"],
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
) -> int | None:
    """Ingest due targets in priority order. None asks the caller to start the next cycle now.

    Every due target spends at most its request budget, so one long backfill cannot
    starve the others. When any target ran out of budget with work left, the worker
    skips its idle wait and starts the next cycle immediately.
    """
    shared_pacer = pacer if pacer is not None else ProviderPacer(stop_requested=stop_requested)
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
    watchlist: MarketDataWatchlistStore | None,
    heartbeat_store: WorkerHeartbeatStore | None,
    now_factory: Callable[[], datetime] | None,
    pacer: ProviderPacer,
) -> IngestOutcome:
    """Run one budgeted ``ingest_once`` and clear a satisfied ingest request."""
    target = plan.target
    key = (target.product_id, target.timeframe)
    try:
        return await ingest_once(
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
        )
    finally:
        if plan.requested and watchlist is not None:
            await _clear_satisfied_request(plan, state_store, watchlist, cycle_now)
        verified_targets.add(key)


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
