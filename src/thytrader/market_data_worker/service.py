"""Core lifecycle for complete-only historical market-data publication."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
import logging
import random
from typing import TYPE_CHECKING, Protocol

from thytrader.market_data.models import CandleInterval, CandleRangeReport
from thytrader.market_data.worker_state import (
    MarketDataMaintenanceKind,
    MarketDataWorkerAttempt,
    MarketDataWorkerError,
    MarketDataWorkerFailure,
    MarketDataWorkerStateStore,
    MarketDataWorkerSuccess,
)

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable

    from thytrader.market_data.datasets import DatasetStore


class HourlyRangeService(Protocol):
    """Provider-neutral bounded historical range capability used by ingestion."""

    async def get_hourly_range(
        self,
        product_id: str,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Return one validated explicit hourly range."""
        ...


async def ingest_once(
    *,
    service: HourlyRangeService,
    dataset_store: DatasetStore,
    state_store: MarketDataWorkerStateStore,
    provider: str,
    product_id: str,
    lookback_hours: int,
    now: datetime,
    retry_base_seconds: int = 300,
    jitter_factory: Callable[[], float] = random.random,
    verify_current_dataset: bool = True,
) -> None:
    """Retrieve, verify, publish, and durably report one bounded hourly range."""
    ends_at = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    prior = await state_store.get(provider, product_id, CandleInterval.ONE_HOUR)
    if prior is not None and prior.complete and prior.covered_ends_at is not None:
        if prior.covered_ends_at >= ends_at:
            reconciliation_attempt = MarketDataWorkerAttempt(
                provider=provider,
                product_id=product_id,
                timeframe=CandleInterval.ONE_HOUR,
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
                return
            covered_starts_at = prior.covered_starts_at
            expected_candle_count = prior.expected_candle_count
            received_candle_count = prior.received_candle_count
            gap_count = prior.gap_count
            missing_intervals = prior.missing_intervals
            content_fingerprint = prior.content_fingerprint
            if (
                not verify_current_dataset
                and prior.failure_code != "dataset_verification_failed"
                and covered_starts_at is not None
                and expected_candle_count is not None
                and received_candle_count is not None
                and gap_count is not None
                and missing_intervals is not None
                and content_fingerprint is not None
            ):
                await state_store.record_success(
                    MarketDataWorkerSuccess(
                        attempt=reconciliation_attempt,
                        covered_starts_at=covered_starts_at,
                        covered_ends_at=prior.covered_ends_at,
                        expected_candle_count=expected_candle_count,
                        received_candle_count=received_candle_count,
                        gap_count=gap_count,
                        missing_intervals=missing_intervals,
                        content_fingerprint=content_fingerprint,
                        advances_revision=False,
                    )
                )
                _logger.info("market_data_ingestion_current")
                return
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
                return
            await state_store.record_success(
                MarketDataWorkerSuccess(
                    attempt=reconciliation_attempt,
                    covered_starts_at=verified_candles[0].starts_at,
                    covered_ends_at=_safe_shift(
                        verified_candles[-1].starts_at,
                        CandleInterval.ONE_HOUR.duration,
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
            return
        starts_at = _safe_shift(
            prior.covered_ends_at,
            -CandleInterval.ONE_HOUR.duration,
            "Market-data worker cannot represent its incremental range start.",
        )
        maintenance_kind = MarketDataMaintenanceKind.INCREMENTAL
    else:
        starts_at = _safe_shift(
            ends_at,
            -timedelta(hours=lookback_hours),
            "Market-data worker cannot represent its initial range start.",
        )
        maintenance_kind = MarketDataMaintenanceKind.INITIAL_BACKFILL
    attempt = MarketDataWorkerAttempt(
        provider=provider,
        product_id=product_id,
        timeframe=CandleInterval.ONE_HOUR,
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
        report = await service.get_hourly_range(product_id, starts_at, ends_at, ends_at)
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


async def run_market_data_worker(
    stop_requested: asyncio.Event,
    *,
    service: HourlyRangeService,
    dataset_store: DatasetStore,
    state_store: MarketDataWorkerStateStore,
    provider: str,
    product_id: str,
    lookback_hours: int,
    interval_seconds: int,
    now_factory: Callable[[], datetime] = lambda: datetime.now(UTC),
    on_readiness_changed: Callable[[bool], None] | None = None,
) -> None:
    """Run scheduled ingestion until a supervisor requests graceful shutdown."""
    if on_readiness_changed is not None:
        on_readiness_changed(True)
    verify_current_dataset = True
    try:
        while not stop_requested.is_set():
            cycle_now = now_factory()
            prior = await state_store.get(provider, product_id, CandleInterval.ONE_HOUR)
            if (
                prior is not None
                and prior.next_retry_at is not None
                and prior.next_retry_at > cycle_now.astimezone(UTC)
            ):
                wait_seconds = max(
                    1,
                    int((prior.next_retry_at - cycle_now.astimezone(UTC)).total_seconds()),
                )
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop_requested.wait(), timeout=wait_seconds)
                continue
            await ingest_once(
                service=service,
                dataset_store=dataset_store,
                state_store=state_store,
                provider=provider,
                product_id=product_id,
                lookback_hours=lookback_hours,
                now=cycle_now,
                retry_base_seconds=interval_seconds,
                verify_current_dataset=verify_current_dataset,
            )
            verify_current_dataset = False
            state = await state_store.get(provider, product_id, CandleInterval.ONE_HOUR)
            wait_seconds = interval_seconds
            if state is not None and state.next_retry_at is not None:
                wait_seconds = max(
                    1,
                    int((state.next_retry_at - cycle_now.astimezone(UTC)).total_seconds()),
                )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_requested.wait(), timeout=wait_seconds)
    finally:
        if on_readiness_changed is not None:
            on_readiness_changed(False)


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
