"""Core lifecycle for complete-only historical market-data publication."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import random
from typing import TYPE_CHECKING, Literal, Protocol

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

    from thytrader.market_data.datasets import DatasetManifest, DatasetStore
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
    """Retrieve, verify, and publish complete coverage, chunking initial backfill by UTC day."""
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
        lookback_hours=lookback_hours,
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
    if maintenance_kind is MarketDataMaintenanceKind.INCREMENTAL:
        extend_fingerprint = (
            prior.content_fingerprint if prior is not None and prior.complete else None
        )
        await _ingest_planned_range(
            service=service,
            dataset_store=dataset_store,
            state_store=state_store,
            provider=provider,
            product_id=product_id,
            timeframe=timeframe,
            attempt=attempt,
            starts_at=starts_at,
            ends_at=ends_at,
            extend_fingerprint=extend_fingerprint,
            retry_at=retry_at,
        )
        return
    if maintenance_kind is MarketDataMaintenanceKind.PREFIX_BACKFILL:
        if prior is None or prior.content_fingerprint is None or prior.covered_starts_at is None:
            await _record_failure(
                state_store,
                attempt,
                code="incomplete_range",
                message="Historical market-data range was incomplete or inconsistent.",
                next_retry_at=retry_at,
            )
            return
        await _ingest_prefix_backfill(
            service=service,
            dataset_store=dataset_store,
            state_store=state_store,
            provider=provider,
            product_id=product_id,
            timeframe=timeframe,
            attempt=attempt,
            lookback_start=starts_at,
            covered_starts_at=prior.covered_starts_at,
            closed_end=ends_at,
            extend_fingerprint=prior.content_fingerprint,
            retry_at=retry_at,
        )
        return
    await _ingest_chunked_backfill(
        service=service,
        dataset_store=dataset_store,
        state_store=state_store,
        provider=provider,
        product_id=product_id,
        timeframe=timeframe,
        attempt=attempt,
        starts_at=starts_at,
        ends_at=ends_at,
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
) -> bool:
    """True when the latest complete island spans the full watch lookback."""
    if not island_complete or covered_starts_at is None or covered_ends_at is None:
        return False
    lookback_start = bounded_lookback_start(closed_end, lookback_hours, interval)
    return covered_starts_at <= lookback_start and covered_ends_at >= closed_end


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
    lookback_start = bounded_lookback_start(ends_at, lookback_hours, timeframe)
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
    """Choose initial backfill, prefix backfill, or one-bar overlap incremental extension."""
    lookback_start = bounded_lookback_start(ends_at, lookback_hours, timeframe)
    if prior is not None and prior.complete and prior.covered_ends_at is not None:
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
class _ChunkProgress:
    """Newest published island plus the fingerprint used to extend the current run."""

    newest: DatasetManifest | None
    island_fingerprint: str | None
    status: Literal["ok", "incomplete", "provider_unavailable", "persist_failed"]


def _utc_day_chunks(
    starts_at: datetime, ends_at: datetime
) -> tuple[tuple[datetime, datetime], ...]:
    """Split a half-open range into UTC-day windows, oldest first, without interpolation."""
    if starts_at >= ends_at:
        return ()
    chunks: list[tuple[datetime, datetime]] = []
    cursor = starts_at
    while cursor < ends_at:
        day_start = cursor.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = _safe_shift(
            day_start,
            timedelta(days=1),
            "Market-data worker cannot represent a UTC-day chunk boundary.",
        )
        chunk_end = ends_at if next_day >= ends_at else next_day
        chunks.append((cursor, chunk_end))
        cursor = chunk_end
    return tuple(chunks)


async def _ingest_planned_range(
    *,
    service: HistoricalRangeService,
    dataset_store: DatasetStore,
    state_store: MarketDataWorkerStateStore,
    provider: str,
    product_id: str,
    timeframe: CandleInterval,
    attempt: MarketDataWorkerAttempt,
    starts_at: datetime,
    ends_at: datetime,
    extend_fingerprint: str | None,
    retry_at: datetime,
) -> None:
    """Fetch one exact window, publish if complete, otherwise fail closed."""
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
    if not _matches_complete_request(report, starts_at, ends_at):
        await _record_failure(
            state_store,
            attempt,
            code="incomplete_range",
            message="Historical market-data range was incomplete or inconsistent.",
            next_retry_at=retry_at,
        )
        _logger.warning("market_data_ingestion_failed code=incomplete_range")
        return
    verified = _persist_complete_range(
        dataset_store=dataset_store,
        provider=provider,
        product_id=product_id,
        extend_fingerprint=extend_fingerprint,
        report=report,
    )
    if verified is None:
        await _record_failure(
            state_store,
            attempt,
            code="dataset_persistence_failed",
            message="Validated market-data publication failed.",
            next_retry_at=retry_at,
        )
        return
    await _record_island_success(state_store, attempt, verified)


async def _ingest_chunked_backfill(
    *,
    service: HistoricalRangeService,
    dataset_store: DatasetStore,
    state_store: MarketDataWorkerStateStore,
    provider: str,
    product_id: str,
    timeframe: CandleInterval,
    attempt: MarketDataWorkerAttempt,
    starts_at: datetime,
    ends_at: datetime,
    retry_at: datetime,
) -> None:
    """Publish complete UTC-day chunks oldest-first; keep the newest contiguous island."""
    newest: DatasetManifest | None = None
    island_fingerprint: str | None = None
    for chunk_start, chunk_end in _utc_day_chunks(starts_at, ends_at):
        progress = await _ingest_one_chunk(
            service=service,
            dataset_store=dataset_store,
            provider=provider,
            product_id=product_id,
            timeframe=timeframe,
            closed_end=ends_at,
            chunk_start=chunk_start,
            chunk_end=chunk_end,
            island_fingerprint=island_fingerprint,
            newest=newest,
        )
        newest = progress.newest
        island_fingerprint = progress.island_fingerprint
        if progress.status in {"provider_unavailable", "persist_failed"}:
            if newest is not None:
                await _record_island_success(state_store, attempt, newest)
                return
            code = (
                "provider_unavailable"
                if progress.status == "provider_unavailable"
                else "dataset_persistence_failed"
            )
            await _record_failure(
                state_store,
                attempt,
                code=code,
                message=(
                    "Historical market-data retrieval failed."
                    if code == "provider_unavailable"
                    else "Validated market-data publication failed."
                ),
                next_retry_at=retry_at,
            )
            return
    if newest is None:
        await _record_failure(
            state_store,
            attempt,
            code="incomplete_range",
            message="Historical market-data range was incomplete or inconsistent.",
            next_retry_at=retry_at,
        )
        _logger.warning("market_data_ingestion_failed code=incomplete_range")
        return
    await _record_island_success(state_store, attempt, newest)


async def _ingest_one_chunk(
    *,
    service: HistoricalRangeService,
    dataset_store: DatasetStore,
    provider: str,
    product_id: str,
    timeframe: CandleInterval,
    closed_end: datetime,
    chunk_start: datetime,
    chunk_end: datetime,
    island_fingerprint: str | None,
    newest: DatasetManifest | None,
) -> _ChunkProgress:
    """Fetch one UTC-day chunk and publish it when the provider range is complete."""
    fetch_start = chunk_start
    if island_fingerprint is not None:
        fetch_start = _safe_shift(
            chunk_start,
            -timeframe.duration,
            "Market-data worker cannot represent a chunk overlap start.",
        )
    try:
        report = await fetch_historical_range(
            service, product_id, timeframe, fetch_start, chunk_end, closed_end
        )
    except Exception:  # noqa: BLE001 - provider boundary is intentionally fail-closed.
        _logger.warning("market_data_ingestion_failed code=provider_unavailable")
        return _ChunkProgress(newest, island_fingerprint, "provider_unavailable")
    if not _matches_complete_request(report, fetch_start, chunk_end):
        _logger.warning("market_data_ingestion_failed code=chunk_incomplete")
        return _ChunkProgress(newest, None, "incomplete")
    verified = _persist_complete_range(
        dataset_store=dataset_store,
        provider=provider,
        product_id=product_id,
        extend_fingerprint=island_fingerprint,
        report=report,
    )
    if verified is None:
        return _ChunkProgress(newest, island_fingerprint, "persist_failed")
    return _ChunkProgress(verified, verified.content_fingerprint, "ok")


async def _ingest_prefix_backfill(
    *,
    service: HistoricalRangeService,
    dataset_store: DatasetStore,
    state_store: MarketDataWorkerStateStore,
    provider: str,
    product_id: str,
    timeframe: CandleInterval,
    attempt: MarketDataWorkerAttempt,
    lookback_start: datetime,
    covered_starts_at: datetime,
    closed_end: datetime,
    extend_fingerprint: str,
    retry_at: datetime,
) -> None:
    """Prepend complete UTC-day chunks onto an existing island; stop at the first hole."""
    try:
        newest = dataset_store.load_manifest(extend_fingerprint)
    except Exception:  # noqa: BLE001
        await _record_failure(
            state_store,
            attempt,
            code="dataset_verification_failed",
            message="The current market-data dataset could not be verified.",
            next_retry_at=retry_at,
        )
        return
    island_fingerprint: str | None = newest.content_fingerprint
    chunks = tuple(reversed(_utc_day_chunks(lookback_start, covered_starts_at)))
    for chunk_start, chunk_end in chunks:
        fetch_end = _safe_shift(
            chunk_end,
            timeframe.duration,
            "Market-data worker cannot represent a prefix overlap end.",
        )
        if fetch_end > closed_end:
            fetch_end = closed_end
        progress = await _ingest_one_prefix_chunk(
            service=service,
            dataset_store=dataset_store,
            provider=provider,
            product_id=product_id,
            timeframe=timeframe,
            closed_end=closed_end,
            chunk_start=chunk_start,
            fetch_end=fetch_end,
            island_fingerprint=island_fingerprint,
            newest=newest,
        )
        newest = progress.newest
        island_fingerprint = progress.island_fingerprint
        if progress.status == "incomplete":
            if newest is not None:
                await _record_island_success(state_store, attempt, newest)
                return
            await _record_failure(
                state_store,
                attempt,
                code="incomplete_range",
                message="Historical market-data range was incomplete or inconsistent.",
                next_retry_at=retry_at,
            )
            return
        if progress.status in {"provider_unavailable", "persist_failed"}:
            if newest is not None:
                await _record_island_success(state_store, attempt, newest)
                return
            code = (
                "provider_unavailable"
                if progress.status == "provider_unavailable"
                else "dataset_persistence_failed"
            )
            await _record_failure(
                state_store,
                attempt,
                code=code,
                message=(
                    "Historical market-data retrieval failed."
                    if code == "provider_unavailable"
                    else "Validated market-data publication failed."
                ),
                next_retry_at=retry_at,
            )
            return
    if newest is None:
        await _record_failure(
            state_store,
            attempt,
            code="incomplete_range",
            message="Historical market-data range was incomplete or inconsistent.",
            next_retry_at=retry_at,
        )
        return
    await _record_island_success(state_store, attempt, newest)


async def _ingest_one_prefix_chunk(
    *,
    service: HistoricalRangeService,
    dataset_store: DatasetStore,
    provider: str,
    product_id: str,
    timeframe: CandleInterval,
    closed_end: datetime,
    chunk_start: datetime,
    fetch_end: datetime,
    island_fingerprint: str | None,
    newest: DatasetManifest | None,
) -> _ChunkProgress:
    """Fetch one prefix day plus one overlapping island bar and prepend when complete."""
    try:
        report = await fetch_historical_range(
            service, product_id, timeframe, chunk_start, fetch_end, closed_end
        )
    except Exception:  # noqa: BLE001 - provider boundary is intentionally fail-closed.
        _logger.warning("market_data_ingestion_failed code=provider_unavailable")
        return _ChunkProgress(newest, island_fingerprint, "provider_unavailable")
    if not _matches_complete_request(report, chunk_start, fetch_end):
        _logger.warning("market_data_ingestion_failed code=chunk_incomplete")
        return _ChunkProgress(newest, island_fingerprint, "incomplete")
    verified = _persist_complete_range(
        dataset_store=dataset_store,
        provider=provider,
        product_id=product_id,
        extend_fingerprint=island_fingerprint,
        report=report,
    )
    if verified is None:
        return _ChunkProgress(newest, island_fingerprint, "persist_failed")
    return _ChunkProgress(verified, verified.content_fingerprint, "ok")


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
) -> None:
    """Record worker coverage for the newest complete contiguous published island."""
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
        if _in_backoff(prior, cycle_now) and not requested:
            continue
        key = (target.product_id, target.timeframe)
        try:
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
        finally:
            if requested and watchlist is not None:
                await watchlist.clear_ingest_request(
                    target.provider, target.product_id, target.timeframe
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
    starts_at: datetime,
    ends_at: datetime,
) -> bool:
    """Require the service report to match one exact complete half-open range."""
    return (
        report.complete
        and report.starts_at == starts_at
        and report.ends_at == ends_at
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
