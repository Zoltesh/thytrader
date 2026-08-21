"""Read-only durable market-data ingestion diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator

from thytrader.api.dependencies import (
    get_market_data_state_store,
    get_market_feed_state_store,
    get_runtime_state,
)
from thytrader.market_data.feed_state import (
    MarketFeedSnapshot,
    MarketFeedStateStore,
)
from thytrader.market_data.freshness import (
    evaluate_freshness,
)
from thytrader.market_data.models import CandleInterval
from thytrader.market_data.worker_state import (
    MarketDataWorkerState,
    MarketDataWorkerStateStore,
    MarketDataWorkerUnavailableError,
    validate_market_data_worker_state,
)
from thytrader.runtime import RuntimeState  # noqa: TC001 - FastAPI resolves annotations at runtime.

router = APIRouter(prefix="/api/v1/market-data", tags=["market-data"])
_logger = logging.getLogger(__name__)


def _require_utc(value: datetime | None) -> datetime | None:
    """Reject naive or non-UTC datetimes so ages cannot be ambiguous."""
    if value is None:
        return None
    if value.tzinfo is not UTC:
        raise ValueError("datetime must be timezone-aware UTC")
    return value


class IngestionCoverageResponse(BaseModel):
    """Last verified complete range and immutable dataset identity."""

    starts_at: datetime
    ends_at: datetime
    expected_candle_count: int
    received_candle_count: int
    gap_count: int
    missing_intervals: int
    complete: bool
    content_fingerprint: str


class IngestionFailureResponse(BaseModel):
    """Stable redacted failure evidence for the most recent attempt."""

    code: str
    message: str
    consecutive_failures: int


class IngestionStateResponse(BaseModel):
    """Durable worker lifecycle, freshness, coverage, and failure evidence."""

    provider: str
    product_id: str
    timeframe: Literal["1h"]
    status: Literal["never_run", "running", "succeeded", "failed"]
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    requested_starts_at: datetime | None
    requested_ends_at: datetime | None
    fresh: bool | None
    coverage: IngestionCoverageResponse | None
    failure: IngestionFailureResponse | None
    enabled: bool
    freshness: Literal["current", "delayed", "stale", "unknown"]
    coverage_status: Literal["complete", "gap_detected", "unavailable"]
    expected_latest_boundary: datetime
    next_attempt_at: datetime | None
    dataset_revision: int
    maintenance_kind: Literal["initial_backfill", "incremental"] | None


class FreshnessResponse(BaseModel):
    """Explicit market data candle freshness state."""

    product_id: str
    newest_candle_at: datetime | None = None
    as_of: datetime
    age_seconds: int | None = None
    status: Literal["fresh", "stale", "unknown"]

    _require_utc_validator = field_validator("newest_candle_at", "as_of", mode="before")(
        _require_utc
    )


class MarketFeedResponse(BaseModel):
    """Public ticker lifecycle facts, distinct from REST candle freshness."""

    product_id: str
    state: Literal[
        "disconnected",
        "connecting",
        "connected",
        "stale",
        "reconnecting",
        "disabled",
    ]
    last_message_at: datetime | None = None
    last_ticker_at: datetime | None = None
    last_price: str | None = None
    updated_at: datetime

    _require_utc_validator = field_validator(
        "last_message_at", "last_ticker_at", "updated_at", mode="before"
    )(_require_utc)


@router.get("/freshness", response_model=FreshnessResponse)
async def get_market_data_freshness(
    store: Annotated[MarketDataWorkerStateStore, Depends(get_market_data_state_store)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    product_id: Annotated[str, Query(pattern=r"^[A-Z0-9]{2,20}-USD$")] = "BTC-USD",
) -> FreshnessResponse:
    """Return explicit market data freshness evaluated against newest verified candle."""
    now = datetime.now(UTC)
    try:
        provider = _provider(runtime)
        state = await store.get(provider, product_id, CandleInterval.ONE_HOUR)
        newest_candle = state.covered_ends_at if state is not None else None
        freshness = evaluate_freshness(
            product_id=product_id, newest_candle_at=newest_candle, now=now
        )
        return FreshnessResponse(
            product_id=freshness.product_id,
            newest_candle_at=freshness.newest_candle_at,
            as_of=freshness.as_of,
            age_seconds=freshness.age_seconds,
            status=freshness.status.value,
        )
    except Exception as error:  # noqa: BLE001 - persistence details are redacted at the API boundary.
        _logger.warning("Freshness evaluation failed: %s", type(error).__name__)
        raise _unavailable() from None


@router.get("/feed", response_model=MarketFeedResponse)
async def get_market_feed_state(
    store: Annotated[MarketFeedStateStore, Depends(get_market_feed_state_store)],
    product_id: Annotated[str, Query(pattern=r"^[A-Z0-9]{2,20}-USD$")] = "BTC-USD",
) -> MarketFeedResponse:
    """Return the latest public ticker lifecycle snapshot."""
    try:
        snapshot = await store.get(product_id)
        if snapshot is None:
            now = datetime.now(UTC)
            return MarketFeedResponse(
                product_id=product_id,
                state="disconnected",
                last_message_at=None,
                last_ticker_at=None,
                last_price=None,
                updated_at=now,
            )
        validated_snapshot = MarketFeedSnapshot(
            product_id=snapshot.product_id,
            state=snapshot.state,
            last_message_at=snapshot.last_message_at,
            last_ticker_at=snapshot.last_ticker_at,
            last_price=snapshot.last_price,
            updated_at=snapshot.updated_at,
        )
        _require_matching_feed_product(validated_snapshot, requested_product_id=product_id)
        return MarketFeedResponse(
            product_id=validated_snapshot.product_id,
            state=validated_snapshot.state.value,
            last_message_at=validated_snapshot.last_message_at,
            last_ticker_at=validated_snapshot.last_ticker_at,
            last_price=(
                str(validated_snapshot.last_price)
                if validated_snapshot.last_price is not None
                else None
            ),
            updated_at=validated_snapshot.updated_at,
        )
    except Exception as error:  # noqa: BLE001 - persistence details are redacted at the API boundary.
        _logger.warning("Market feed state query failed: %s", type(error).__name__)
        raise _unavailable() from None


@router.get("/ingestion", response_model=IngestionStateResponse)
async def get_ingestion_state(
    store: Annotated[MarketDataWorkerStateStore, Depends(get_market_data_state_store)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    product_id: Annotated[str, Query(pattern=r"^[A-Z0-9]{2,20}-USD$")] = "BTC-USD",
) -> IngestionStateResponse:
    """Return durable ingestion evidence without initiating or mutating worker activity."""
    try:
        provider = _provider(runtime)
        state = await store.get(provider, product_id, CandleInterval.ONE_HOUR)
        if state is not None:
            validate_market_data_worker_state(state)
    except MarketDataWorkerUnavailableError:
        raise _unavailable() from None
    except Exception:  # noqa: BLE001 - persistence details are redacted at the API boundary.
        raise _unavailable() from None
    if state is None:
        expected_boundary = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        return IngestionStateResponse(
            provider=provider,
            product_id=product_id,
            timeframe="1h",
            status="never_run",
            last_attempt_at=None,
            last_success_at=None,
            requested_starts_at=None,
            requested_ends_at=None,
            fresh=None,
            coverage=None,
            failure=None,
            enabled=True,
            freshness="unknown",
            coverage_status="unavailable",
            expected_latest_boundary=expected_boundary,
            next_attempt_at=None,
            dataset_revision=0,
            maintenance_kind=None,
        )
    try:
        return _to_response(
            state,
            now=datetime.now(UTC),
            interval_seconds=runtime.settings.market_data_worker_interval_seconds,
        )
    except Exception as error:  # noqa: BLE001 - mapping failures are redacted at the API boundary.
        _logger.warning("Ingestion state mapping failed: %s", type(error).__name__)
        raise _unavailable() from None


def _to_response(
    state: MarketDataWorkerState,
    *,
    now: datetime,
    interval_seconds: int,
) -> IngestionStateResponse:
    """Map durable state to redacted browser-safe freshness and coverage facts."""
    coverage = _coverage(state)
    failure = (
        IngestionFailureResponse(
            code=state.failure_code,
            message=state.failure_message,
            consecutive_failures=state.consecutive_failures,
        )
        if state.failure_code is not None and state.failure_message is not None
        else None
    )
    expected_boundary = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    freshness = _freshness(state.covered_ends_at, expected_boundary)
    fresh = freshness in {"current", "delayed"} if state.covered_ends_at is not None else None
    coverage_status: Literal["complete", "gap_detected", "unavailable"] = (
        "unavailable"
        if state.failure_code == "dataset_verification_failed"
        else "complete"
        if coverage is not None and state.complete
        else "gap_detected"
        if state.failure_code == "incomplete_range"
        else "unavailable"
    )
    next_attempt_at = state.next_retry_at or state.last_attempt_at + timedelta(
        seconds=interval_seconds
    )
    return IngestionStateResponse(
        provider=state.provider,
        product_id=state.product_id,
        timeframe="1h",
        status=state.status.value,
        last_attempt_at=state.last_attempt_at,
        last_success_at=state.last_success_at,
        requested_starts_at=state.requested_starts_at,
        requested_ends_at=state.requested_ends_at,
        fresh=fresh,
        coverage=coverage,
        failure=failure,
        enabled=state.enabled,
        freshness=freshness,
        coverage_status=coverage_status,
        expected_latest_boundary=expected_boundary,
        next_attempt_at=next_attempt_at,
        dataset_revision=state.dataset_revision,
        maintenance_kind=state.maintenance_kind.value,
    )


def _freshness(
    covered_ends_at: datetime | None,
    expected_boundary: datetime,
) -> Literal["current", "delayed", "stale", "unknown"]:
    """Classify verified coverage against the latest finalized hourly boundary."""
    if covered_ends_at is None:
        return "unknown"
    lag = expected_boundary - covered_ends_at
    if lag <= timedelta(0):
        return "current"
    if lag <= CandleInterval.ONE_HOUR.duration * 2:
        return "delayed"
    return "stale"


def _coverage(state: MarketDataWorkerState) -> IngestionCoverageResponse | None:
    """Return coverage only when every verified publication fact is present."""
    starts_at = state.covered_starts_at
    ends_at = state.covered_ends_at
    expected = state.expected_candle_count
    received = state.received_candle_count
    gap_count = state.gap_count
    missing = state.missing_intervals
    fingerprint = state.content_fingerprint
    if (
        starts_at is None
        or ends_at is None
        or expected is None
        or received is None
        or gap_count is None
        or missing is None
        or fingerprint is None
    ):
        return None
    return IngestionCoverageResponse(
        starts_at=starts_at,
        ends_at=ends_at,
        expected_candle_count=expected,
        received_candle_count=received,
        gap_count=gap_count,
        missing_intervals=missing,
        complete=state.complete,
        content_fingerprint=fingerprint,
    )


def _require_matching_feed_product(
    snapshot: MarketFeedSnapshot,
    *,
    requested_product_id: str,
) -> None:
    """Reject a store snapshot that does not belong to the requested product."""
    if snapshot.product_id != requested_product_id:
        raise ValueError("Feed snapshot product does not match the requested product.")


def _unavailable() -> HTTPException:
    """Build stable diagnostics for unavailable durable worker state."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "market_data_worker_state_unavailable",
            "message": "Market-data ingestion state is unavailable.",
        },
    )


def _provider(runtime: RuntimeState) -> Literal["coinbase", "demo"]:
    """Match diagnostics to the worker's explicit live or demo provenance."""
    settings = runtime.settings
    if settings.coinbase_api_key_name is None or settings.coinbase_api_private_key is None:
        return "demo"
    return "coinbase"
