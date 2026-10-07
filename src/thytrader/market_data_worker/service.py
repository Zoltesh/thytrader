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

This module keeps the worker entry points and cycle orchestration (``ingest_once``,
``run_market_data_worker`` and their planning helpers) and re-exports the names
other modules and tests import from it. Shared contracts live in ``contracts``,
target planning and retry scheduling in ``targets``, the page walks in ``walk``, and
publication and state recording in ``recording``.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import random
from typing import TYPE_CHECKING

from thytrader.market_data.models import HISTORICAL_REQUEST_MAX_CANDLES, CandleInterval
from thytrader.market_data.watch_coverage import (
    bounded_lookback_start,
    island_covers_watch,
    safe_shift,
    watch_lookback_start,
)
from thytrader.market_data.worker_state import (
    MarketDataMaintenanceKind,
    MarketDataWorkerAttempt,
    MarketDataWorkerState,
    MarketDataWorkerStateStore,
    MarketDataWorkerSuccess,
)
from thytrader.market_data_worker.contracts import (
    HistoricalRangeService,
    HourlyRangeService,
    IngestOutcome,
    IngestStop,
    IntervalRangeService,
    _logger,
    _RequestBudget,
    _touch_market_data_heartbeat,
    _WalkContext,
    fetch_historical_range,
)
from thytrader.market_data_worker.pacing import PROVIDER_REQUEST_PAUSE_SECONDS, ProviderPacer
from thytrader.market_data_worker.targets import (
    INGEST_REQUESTS_PER_REQUESTED_TARGET_CYCLE,
    INGEST_REQUESTS_PER_TARGET_CYCLE,
    _clear_satisfied_request,
    _cycle_targets,
    _load_validated_state,
    _next_retry_at,
    _next_wait_seconds,
    _plan_targets,
    _record_failure,
    _TargetPlan,
    _TargetPriority,
)
from thytrader.market_data_worker.walk import LISTING_SEARCH_REQUEST_ALLOWANCE, _walk

if TYPE_CHECKING:
    from collections.abc import Callable

    from thytrader.market_data.datasets import DatasetStore
    from thytrader.market_data.watchlist import MarketDataWatchlistStore, MarketDataWatchTarget
    from thytrader.market_data_worker.retention import DatasetRetentionRunner
    from thytrader.persistence.worker_heartbeats import WorkerHeartbeatStore
    from thytrader.settings_yaml import SettingsStore

__all__ = [
    "INGEST_REQUESTS_PER_REQUESTED_TARGET_CYCLE",
    "INGEST_REQUESTS_PER_TARGET_CYCLE",
    "LISTING_SEARCH_REQUEST_ALLOWANCE",
    "HistoricalRangeService",
    "HourlyRangeService",
    "IngestOutcome",
    "IngestStop",
    "IntervalRangeService",
    "_TargetPriority",
    "_ingest_due_targets",
    "_next_retry_at",
    "_plan_targets",
    "fetch_historical_range",
    "ingest_once",
    "run_market_data_worker",
]


# Stops after which a re-proven provider-history floor is settled for this process: the
# walk either reconciled, anchored the lookback, or re-ran the listing search.
_FLOOR_PROVEN_STOPS = frozenset({IngestStop.CURRENT, IngestStop.COMPLETE, IngestStop.LISTING_FLOOR})


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
        next_attempt_at=safe_shift(
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
        next_attempt_at=safe_shift(
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
            covered_ends_at=safe_shift(
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
        starts_at = safe_shift(
            prior.covered_ends_at,
            -timeframe.duration,
            "Market-data worker cannot represent its incremental range start.",
        )
        return starts_at, MarketDataMaintenanceKind.INCREMENTAL
    return lookback_start, MarketDataMaintenanceKind.INITIAL_BACKFILL


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
