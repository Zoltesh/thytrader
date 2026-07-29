"""Core lifecycle for complete-only historical market-data publication."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Protocol

from thytrader.market_data.models import CandleInterval, CandleRangeReport
from thytrader.market_data.worker_state import (
    MarketDataWorkerAttempt,
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
) -> None:
    """Retrieve, verify, publish, and durably report one bounded hourly range."""
    ends_at = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    starts_at = ends_at - timedelta(hours=lookback_hours)
    attempt = MarketDataWorkerAttempt(
        provider=provider,
        product_id=product_id,
        timeframe=CandleInterval.ONE_HOUR,
        attempted_at=now.astimezone(UTC),
        requested_starts_at=starts_at,
        requested_ends_at=ends_at,
    )
    await state_store.record_attempt(attempt)

    try:
        report = await service.get_hourly_range(product_id, starts_at, ends_at, ends_at)
    except Exception:  # noqa: BLE001 - provider boundary is intentionally fail-closed.
        await _record_failure(
            state_store,
            attempt,
            code="provider_unavailable",
            message="Historical market-data retrieval failed.",
        )
        _logger.warning("market_data_ingestion_failed code=provider_unavailable")
        return

    if not _matches_complete_request(report, attempt):
        await _record_failure(
            state_store,
            attempt,
            code="incomplete_range",
            message="Historical market-data range was incomplete or inconsistent.",
        )
        _logger.warning("market_data_ingestion_failed code=incomplete_range")
        return

    try:
        published = dataset_store.write(provider, product_id, report)
        verified = dataset_store.load_verified(published.manifest_path)
    except Exception:  # noqa: BLE001 - persistence boundary is intentionally fail-closed.
        await _record_failure(
            state_store,
            attempt,
            code="dataset_persistence_failed",
            message="Validated market-data publication failed.",
        )
        _logger.warning("market_data_ingestion_failed code=dataset_persistence_failed")
        return

    await state_store.record_success(
        MarketDataWorkerSuccess(
            attempt=attempt,
            covered_starts_at=report.starts_at,
            covered_ends_at=report.ends_at,
            expected_candle_count=report.requested_candle_count,
            received_candle_count=report.quality.candle_count,
            gap_count=report.quality.gap_count,
            missing_intervals=report.quality.missing_intervals,
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
    try:
        while not stop_requested.is_set():
            await ingest_once(
                service=service,
                dataset_store=dataset_store,
                state_store=state_store,
                provider=provider,
                product_id=product_id,
                lookback_hours=lookback_hours,
                now=now_factory(),
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_requested.wait(), timeout=interval_seconds)
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
) -> None:
    """Persist one stable redacted failure outcome."""
    await state_store.record_failure(
        MarketDataWorkerFailure(attempt=attempt, code=code, message=message)
    )
