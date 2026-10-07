"""Publishing, recording and logging of one ingest walk's outcome.

Persists and reads back a complete segment, records island success, unchanged
coverage or a redacted failure in worker state, and emits the walk's warnings.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from thytrader.market_data.no_trade import count_no_trade_bars
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.watch_coverage import safe_shift
from thytrader.market_data.worker_state import (
    MarketDataWorkerAttempt,
    MarketDataWorkerStateStore,
    MarketDataWorkerSuccess,
)
from thytrader.market_data_worker.contracts import (
    IngestOutcome,
    IngestStop,
    _Direction,
    _Island,
    _logger,
    _WalkContext,
)
from thytrader.market_data_worker.pages import run_end
from thytrader.market_data_worker.targets import _record_failure

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from thytrader.market_data.datasets import DatasetManifest, DatasetStore
    from thytrader.market_data.models import Candle, CandleInterval, CandleRangeReport


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
    return safe_shift(
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
