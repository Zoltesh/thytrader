"""Read-only durable market-data ingestion diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from thytrader.api.dependencies import get_market_data_state_store, get_runtime_state
from thytrader.market_data.models import CandleInterval
from thytrader.market_data.worker_state import (
    MarketDataWorkerState,
    MarketDataWorkerStateStore,
    MarketDataWorkerUnavailableError,
)
from thytrader.runtime import RuntimeState  # noqa: TC001 - FastAPI resolves annotations at runtime.

router = APIRouter(prefix="/api/v1/market-data", tags=["market-data"])


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
    except MarketDataWorkerUnavailableError:
        raise _unavailable() from None
    except Exception:  # noqa: BLE001 - persistence details are redacted at the API boundary.
        raise _unavailable() from None
    if state is None:
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
        )
    return _to_response(state, now=datetime.now(UTC))


def _to_response(state: MarketDataWorkerState, *, now: datetime) -> IngestionStateResponse:
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
    fresh = (
        state.covered_ends_at <= now
        and now - state.covered_ends_at <= CandleInterval.ONE_HOUR.duration * 2
        if state.covered_ends_at is not None
        else None
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
    )


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
