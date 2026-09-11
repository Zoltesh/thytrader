"""Application service for watchlist mutations and complete-only ingest."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader.data_control.models import (
    DataControlError,
    GapObservation,
    classify_gap,
    require_interval,
)
from thytrader.market_data.quality import missing_interval_starts
from thytrader.market_data.watchlist import (
    MarketDataWatchlistError,
    MarketDataWatchlistStore,
    MarketDataWatchlistUnavailableError,
    MarketDataWatchTarget,
)
from thytrader.market_data.worker_state import (
    MarketDataWorkerState,
    MarketDataWorkerStateStore,
    MarketDataWorkerUnavailableError,
)
from thytrader.market_data_worker.service import (
    bounded_lookback_start,
    fetch_historical_range,
)
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)

if TYPE_CHECKING:
    from thytrader.config import Settings
    from thytrader.market_data.datasets import DatasetStore
    from thytrader.market_data.models import CandleInterval
    from thytrader.market_data.service import MarketDataService
    from thytrader.market_data_worker.service import HistoricalRangeService


def ingestion_provider(settings: Settings) -> str:
    """Match worker provenance: live Coinbase when credentials exist, otherwise demo."""
    if settings.coinbase_api_key_name is None or settings.coinbase_api_private_key is None:
        return "demo"
    return "coinbase"


async def list_watch_targets(store: MarketDataWatchlistStore) -> tuple[MarketDataWatchTarget, ...]:
    """Return the durable watchlist or fail closed."""
    try:
        return await store.list_all()
    except MarketDataWatchlistUnavailableError as error:
        raise DataControlError("Market-data watchlist is unavailable.") from error


async def add_watch_target(
    *,
    store: MarketDataWatchlistStore,
    market_data: MarketDataService,
    audit: AuditEventStore,
    settings: Settings,
    product_id: str,
    timeframe: str,
    lookback_hours: int,
    enabled: bool,
    now: datetime,
) -> MarketDataWatchTarget:
    """Validate a USD spot product and upsert one watchlist row."""
    interval = require_interval(timeframe)
    await _require_usd_spot_product(market_data, product_id)
    provider = ingestion_provider(settings)
    target = MarketDataWatchTarget(
        provider=provider,
        product_id=product_id,
        timeframe=interval,
        lookback_hours=lookback_hours,
        enabled=enabled,
        updated_at=now.astimezone(UTC),
    )
    try:
        stored = await store.upsert(target)
    except (MarketDataWatchlistError, MarketDataWatchlistUnavailableError) as error:
        raise DataControlError(str(error)) from error
    await _audit(
        audit,
        action="watch_add",
        product_id=product_id,
        detail=f"timeframe={interval.value} lookback_hours={lookback_hours} enabled={enabled}",
        provider=provider,
        now=now,
    )
    return stored


async def ingest_target(
    *,
    watchlist: MarketDataWatchlistStore,
    state_store: MarketDataWorkerStateStore,
    audit: AuditEventStore,
    settings: Settings,
    product_id: str,
    timeframe: str,
    now: datetime,
) -> tuple[MarketDataWatchTarget, MarketDataWorkerState | None]:
    """Queue complete-only ingest for the market-data worker. Does not write Parquet."""
    interval = require_interval(timeframe)
    provider = ingestion_provider(settings)
    lookback_hours = await _lookback_hours(watchlist, settings, provider, product_id, interval)
    try:
        target = await watchlist.request_ingest(
            provider=provider,
            product_id=product_id,
            timeframe=interval,
            lookback_hours=lookback_hours,
            now=now,
        )
        state = await state_store.get(provider, product_id, interval)
    except (MarketDataWatchlistError, MarketDataWatchlistUnavailableError) as error:
        raise DataControlError(str(error)) from error
    except MarketDataWorkerUnavailableError as error:
        raise DataControlError("Market-data worker state is unavailable.") from error
    await _audit(
        audit,
        action="ingest_requested",
        product_id=product_id,
        detail=f"timeframe={interval.value} lookback_hours={lookback_hours}",
        provider=provider,
        now=now,
    )
    return target, state


async def ingest_status(
    *,
    watchlist: MarketDataWatchlistStore,
    state_store: MarketDataWorkerStateStore,
    settings: Settings,
    product_id: str,
    timeframe: str,
) -> tuple[MarketDataWatchTarget | None, MarketDataWorkerState | None]:
    """Return the current ingest request flag and worker coverage state."""
    interval = require_interval(timeframe)
    provider = ingestion_provider(settings)
    try:
        target = await watchlist.get(provider, product_id, interval)
        state = await state_store.get(provider, product_id, interval)
    except MarketDataWatchlistUnavailableError as error:
        raise DataControlError("Market-data watchlist is unavailable.") from error
    except MarketDataWorkerUnavailableError as error:
        raise DataControlError("Market-data worker state is unavailable.") from error
    return target, state


async def inspect_gaps(
    *,
    service: HistoricalRangeService,
    dataset_store: DatasetStore,
    state_store: MarketDataWorkerStateStore,
    watchlist: MarketDataWatchlistStore,
    settings: Settings,
    product_id: str,
    timeframe: str,
    now: datetime,
) -> tuple[datetime, datetime, tuple[GapObservation, ...], str | None]:
    """Probe the exchange and classify missing bars without writing Parquet."""
    interval = require_interval(timeframe)
    provider = ingestion_provider(settings)
    lookback_hours = await _lookback_hours(watchlist, settings, provider, product_id, interval)
    ends_at = interval.align_closed_end(now)
    starts_at = bounded_lookback_start(ends_at, lookback_hours, interval)
    local_starts = _local_starts(dataset_store, provider, product_id, interval)
    try:
        state = await state_store.get(provider, product_id, interval)
    except MarketDataWorkerUnavailableError:
        state = None
    exchange_starts, probe_warning = await _probe_starts(
        service, product_id, interval, starts_at, ends_at
    )
    expected = missing_interval_starts((), interval, starts_at, ends_at)
    observations = tuple(
        _observation_for(
            start=start,
            local_starts=local_starts,
            exchange_starts=exchange_starts,
            state=state,
        )
        for start in expected
    )
    gaps = tuple(item for item in observations if item is not None)
    return starts_at, ends_at, gaps, probe_warning


async def _require_usd_spot_product(market_data: MarketDataService, product_id: str) -> None:
    """Reject products that are not enabled USD spot in the current catalog."""
    try:
        products = await market_data.list_enabled_usd_spot_products()
    except Exception as error:
        raise DataControlError("The USD spot product catalog could not be loaded.") from error
    if not any(product.product_id == product_id for product in products):
        raise DataControlError(f"{product_id} is not an enabled USD spot product.")


async def _lookback_hours(
    watchlist: MarketDataWatchlistStore,
    settings: Settings,
    provider: str,
    product_id: str,
    interval: CandleInterval,
) -> int:
    """Prefer the watchlist lookback, otherwise the worker setting."""
    try:
        target = await watchlist.get(provider, product_id, interval)
    except MarketDataWatchlistUnavailableError:
        target = None
    if target is not None:
        return target.lookback_hours
    return settings.market_data_worker_lookback_hours


def _local_starts(
    dataset_store: DatasetStore,
    provider: str,
    product_id: str,
    interval: CandleInterval,
) -> set[datetime]:
    """Return verified local bar starts for one target, if a complete dataset exists."""
    latest = {
        (item.provider, item.product_id, item.timeframe): item
        for item in dataset_store.list_latest_verified()
    }
    manifest = latest.get((provider, product_id, interval.value))
    if manifest is None:
        return set()
    try:
        candles = dataset_store.load_candles(manifest.content_fingerprint)
    except Exception:  # noqa: BLE001 - corrupt local files are treated as uncovered.
        return set()
    return {candle.starts_at for candle in candles}


async def _probe_starts(
    service: HistoricalRangeService,
    product_id: str,
    interval: CandleInterval,
    starts_at: datetime,
    ends_at: datetime,
) -> tuple[set[datetime] | None, str | None]:
    """Fetch one diagnostic range without publishing it."""
    try:
        report = await fetch_historical_range(
            service, product_id, interval, starts_at, ends_at, ends_at
        )
    except Exception:  # noqa: BLE001 - probe failures are classified, not raised as coverage.
        return None, "Exchange range probe failed; remaining gaps are not marked exchange-empty."
    return {candle.starts_at for candle in report.quality.candles}, None


def _observation_for(
    *,
    start: datetime,
    local_starts: set[datetime],
    exchange_starts: set[datetime] | None,
    state: MarketDataWorkerState | None,
) -> GapObservation | None:
    """Classify one expected bar or omit it when local coverage already has it."""
    present_on_exchange = None if exchange_starts is None else start in exchange_starts
    cause = classify_gap(
        present_locally=start in local_starts,
        present_on_exchange=present_on_exchange,
        worker_attempted=state is not None,
        worker_complete=bool(state is not None and state.complete),
    )
    if cause is None:
        return None
    return GapObservation(starts_at=start, cause=cause)


async def _audit(
    audit: AuditEventStore,
    *,
    action: str,
    product_id: str,
    detail: str,
    provider: str,
    now: datetime,
) -> None:
    """Record a market-data mutation without secrets."""
    event = AuditEvent(
        occurred_at=now.astimezone(UTC),
        category=AuditEventCategory.MARKET_DATA,
        action=action,
        outcome=AuditEventOutcome.SUCCESS,
        detail=detail,
        provider=provider,
        product_id=product_id,
    )
    await audit.append(event)


def watch_payload(target: MarketDataWatchTarget) -> dict[str, object]:
    """Serialize one watch target for HTTP and CLI JSON."""
    return {
        "provider": target.provider,
        "product_id": target.product_id,
        "timeframe": target.timeframe.value,
        "lookback_hours": target.lookback_hours,
        "enabled": target.enabled,
        "updated_at": target.updated_at.isoformat(),
        "ingest_requested_at": (
            target.ingest_requested_at.isoformat() if target.ingest_requested_at else None
        ),
    }


def gap_payload(item: GapObservation) -> dict[str, str]:
    """Serialize one classified missing bar."""
    return {"starts_at": item.starts_at.isoformat(), "cause": item.cause.value}


def worker_state_payload(state: MarketDataWorkerState | None) -> dict[str, object]:
    """Serialize durable ingest outcome without candle payloads."""
    if state is None:
        return {"status": "never_run", "complete": False}
    return {
        "status": state.status.value,
        "complete": state.complete,
        "failure_code": state.failure_code,
        "covered_starts_at": (
            state.covered_starts_at.isoformat() if state.covered_starts_at else None
        ),
        "covered_ends_at": state.covered_ends_at.isoformat() if state.covered_ends_at else None,
        "content_fingerprint": state.content_fingerprint,
        "expected_candle_count": state.expected_candle_count,
        "received_candle_count": state.received_candle_count,
        "gap_count": state.gap_count,
        "missing_intervals": state.missing_intervals,
    }
