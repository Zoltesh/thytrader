"""Core lifecycle for complete-only historical market-data publication."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
import logging
import random
from typing import TYPE_CHECKING, Protocol

from thytrader.market_data.models import (
    MAX_HISTORICAL_INTERVAL_COUNT,
    CandleInterval,
    CandleRangeReport,
)
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

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable

    from thytrader.market_data.datasets import DatasetStore
    from thytrader.persistence.worker_heartbeats import WorkerHeartbeatStore


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
) -> None:
    """Retrieve, verify, publish, and durably report one bounded complete range."""
    ends_at = timeframe.align_closed_end(now)
    prior = await _load_validated_state(state_store, provider, product_id, timeframe)
    if await _reconcile_current_coverage(
        service=service,
        dataset_store=dataset_store,
        state_store=state_store,
        provider=provider,
        product_id=product_id,
        timeframe=timeframe,
        prior=prior,
        ends_at=ends_at,
        now=now,
        retry_base_seconds=retry_base_seconds,
        jitter_factory=jitter_factory,
        verify_current_dataset=verify_current_dataset,
    ):
        return
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
        return
    retry_at = _next_retry_at(
        attempt.attempted_at,
        retry_base_seconds,
        prior.consecutive_failures if prior is not None else 0,
        jitter_factory(),
    )
    try:
        report = await fetch_historical_range(
            service, product_id, timeframe, starts_at, ends_at, ends_at
        )
    except Exception:  # noqa: BLE001 - provider boundary is intentionally fail-closed.
        await _record_failure(
            state_store,
            attempt,
            code="provider_unavailable",
            message="Historical market-data retrieval failed.",
            next_retry_at=retry_at,
        )
        _logger.warning("market_data_ingestion_failed code=provider_unavailable")
        return
    if not _matches_complete_request(report, attempt):
        await _record_failure(
            state_store,
            attempt,
            code="incomplete_range",
            message="Historical market-data range was incomplete or inconsistent.",
            next_retry_at=retry_at,
        )
        _logger.warning("market_data_ingestion_failed code=incomplete_range")
        return
    await _publish_verified_range(
        dataset_store=dataset_store,
        state_store=state_store,
        provider=provider,
        product_id=product_id,
        prior=prior,
        attempt=attempt,
        report=report,
        retry_at=retry_at,
    )


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
) -> None:
    """Run scheduled ingestion until a supervisor requests graceful shutdown."""
    if on_readiness_changed is not None:
        on_readiness_changed(True)
    verified_targets: set[tuple[str, CandleInterval]] = set()
    try:
        while not stop_requested.is_set():
            cycle_now = now_factory()
            if heartbeat_store is not None:
                await heartbeat_store.touch("market_data_worker", cycle_now.astimezone(UTC))
            targets = await _cycle_targets(
                watchlist,
                provider=provider,
                product_id=product_id,
                timeframe=timeframe,
                lookback_hours=lookback_hours,
                now=cycle_now,
            )
            wait_seconds = await _ingest_due_targets(
                targets,
                service=service,
                dataset_store=dataset_store,
                state_store=state_store,
                interval_seconds=interval_seconds,
                cycle_now=cycle_now,
                verified_targets=verified_targets,
                stop_requested=stop_requested,
                watchlist=watchlist,
            )
            if stop_requested.is_set() or wait_seconds is None:
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_requested.wait(), timeout=wait_seconds)
    finally:
        if on_readiness_changed is not None:
            on_readiness_changed(False)


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
    now: datetime,
    retry_base_seconds: int,
    jitter_factory: Callable[[], float],
    verify_current_dataset: bool,
) -> bool:
    """Return True when coverage is already current and no fetch is required."""
    del service
    if prior is None or not prior.complete or prior.covered_ends_at is None:
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
    """Choose initial backfill versus one-bar overlap incremental extension."""
    if prior is not None and prior.complete and prior.covered_ends_at is not None:
        starts_at = _safe_shift(
            prior.covered_ends_at,
            -timeframe.duration,
            "Market-data worker cannot represent its incremental range start.",
        )
        return starts_at, MarketDataMaintenanceKind.INCREMENTAL
    return (
        bounded_lookback_start(ends_at, lookback_hours, timeframe),
        MarketDataMaintenanceKind.INITIAL_BACKFILL,
    )


async def _publish_verified_range(
    *,
    dataset_store: DatasetStore,
    state_store: MarketDataWorkerStateStore,
    provider: str,
    product_id: str,
    prior: MarketDataWorkerState | None,
    attempt: MarketDataWorkerAttempt,
    report: CandleRangeReport,
    retry_at: datetime,
) -> None:
    """Write or extend Parquet through DatasetStore and record success."""
    try:
        published = (
            dataset_store.extend(prior.content_fingerprint, report)
            if prior is not None
            and prior.complete
            and prior.content_fingerprint is not None
            and prior.covered_ends_at is not None
            else dataset_store.write(provider, product_id, report)
        )
        verified = dataset_store.load_verified(published.manifest_path)
    except Exception:  # noqa: BLE001 - persistence boundary is intentionally fail-closed.
        await _record_failure(
            state_store,
            attempt,
            code="dataset_persistence_failed",
            message="Validated market-data publication failed.",
            next_retry_at=retry_at,
        )
        _logger.warning("market_data_ingestion_failed code=dataset_persistence_failed")
        return
    await state_store.record_success(
        MarketDataWorkerSuccess(
            attempt=attempt,
            covered_starts_at=datetime.fromisoformat(verified.starts_at.replace("Z", "+00:00")),
            covered_ends_at=datetime.fromisoformat(verified.ends_at.replace("Z", "+00:00")),
            expected_candle_count=verified.expected_candle_count,
            received_candle_count=verified.received_candle_count,
            gap_count=verified.gap_count,
            missing_intervals=verified.missing_intervals,
            content_fingerprint=verified.content_fingerprint,
        )
    )
    _logger.info("market_data_ingestion_succeeded")


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
) -> int | None:
    """Ingest due targets. None means the caller should immediately re-check stop."""
    for target in targets:
        if stop_requested.is_set():
            break
        prior = await _load_validated_state(
            state_store, target.provider, target.product_id, target.timeframe
        )
        requested = target.ingest_requested_at is not None
        if requested and watchlist is not None:
            await watchlist.clear_ingest_request(
                target.provider, target.product_id, target.timeframe
            )
        if _in_backoff(prior, cycle_now) and not requested:
            continue
        key = (target.product_id, target.timeframe)
        await ingest_once(
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
        )
        verified_targets.add(key)
    return await _next_wait_seconds(state_store, targets, cycle_now, interval_seconds)


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


def _matches_complete_request(
    report: CandleRangeReport,
    attempt: MarketDataWorkerAttempt,
) -> bool:
    """Require the service report to match the worker's exact complete request."""
    return (
        report.complete
        and report.starts_at == attempt.requested_starts_at
        and report.ends_at == attempt.requested_ends_at
        and report.requested_candle_count == report.quality.candle_count
        and report.quality.gap_count == 0
        and report.quality.missing_intervals == 0
    )


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
