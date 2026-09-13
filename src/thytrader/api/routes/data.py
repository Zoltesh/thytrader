"""Confirmation-gated watchlist and complete-only ingest HTTP contract."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from thytrader.api.dependencies import (
    get_audit_event_store,
    get_dataset_store,
    get_market_data_service,
    get_market_data_state_store,
    get_market_data_watchlist_store,
    get_runtime_state,
)
from thytrader.data_control.models import (
    DataControlError,
    IngestRequest,
    WatchTargetRequest,
    require_interval,
)
from thytrader.data_control.service import (
    add_watch_target,
    gap_payload,
    ingest_status,
    ingest_target,
    inspect_gaps,
    list_watch_targets,
    watch_payload,
    worker_state_payload,
)
from thytrader.market_data.datasets import DatasetStore  # noqa: TC001
from thytrader.market_data.models import DatasetTimeframe  # noqa: TC001
from thytrader.market_data.service import MarketDataService  # noqa: TC001
from thytrader.market_data.watchlist import MarketDataWatchlistStore  # noqa: TC001
from thytrader.market_data.worker_state import MarketDataWorkerStateStore  # noqa: TC001
from thytrader.persistence.audit_events import AuditEventStore  # noqa: TC001
from thytrader.runtime import RuntimeState  # noqa: TC001

router = APIRouter(prefix="/api/v1/data", tags=["data"])
_MAX_LISTED_GAPS = 200


@router.get("/watchlist")
async def get_watchlist(
    store: Annotated[MarketDataWatchlistStore, Depends(get_market_data_watchlist_store)],
) -> dict[str, object]:
    """Return every watchlist row without candle payloads."""
    try:
        targets = await list_watch_targets(store)
    except DataControlError as error:
        raise _http_error(error) from None
    return {"targets": [watch_payload(target) for target in targets]}


@router.put("/watchlist")
async def put_watch_target(
    body: WatchTargetRequest,
    store: Annotated[MarketDataWatchlistStore, Depends(get_market_data_watchlist_store)],
    market_data: Annotated[MarketDataService, Depends(get_market_data_service)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
) -> dict[str, object]:
    """Upsert one USD spot product and timeframe onto the ingestion watchlist."""
    try:
        target = await add_watch_target(
            store=store,
            market_data=market_data,
            audit=audit,
            settings=runtime.settings,
            product_id=body.product_id,
            timeframe=body.timeframe,
            lookback_hours=body.lookback_hours,
            enabled=body.enabled,
            now=datetime.now(UTC),
        )
    except DataControlError as error:
        raise _http_error(error) from None
    return {"target": watch_payload(target)}


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def post_ingest(
    body: IngestRequest,
    state_store: Annotated[MarketDataWorkerStateStore, Depends(get_market_data_state_store)],
    watchlist: Annotated[MarketDataWatchlistStore, Depends(get_market_data_watchlist_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
) -> dict[str, object]:
    """Queue complete-only ingest for the market-data worker. Does not write Parquet."""
    try:
        target, state = await ingest_target(
            watchlist=watchlist,
            state_store=state_store,
            audit=audit,
            settings=runtime.settings,
            product_id=body.product_id,
            timeframe=body.timeframe,
            now=datetime.now(UTC),
        )
    except DataControlError as error:
        raise _http_error(error) from None
    return {
        "accepted": True,
        "product_id": body.product_id,
        "timeframe": body.timeframe,
        "ingest_requested_at": (
            target.ingest_requested_at.isoformat() if target.ingest_requested_at else None
        ),
        "state": worker_state_payload(
            state,
            lookback_hours=target.lookback_hours,
            interval=require_interval(body.timeframe),
            now=datetime.now(UTC),
        ),
    }


@router.get("/ingest")
async def get_ingest(
    state_store: Annotated[MarketDataWorkerStateStore, Depends(get_market_data_state_store)],
    watchlist: Annotated[MarketDataWatchlistStore, Depends(get_market_data_watchlist_store)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    product_id: Annotated[str, Query(pattern=r"^[A-Z0-9]{2,20}-USD$")],
    timeframe: Annotated[DatasetTimeframe, Query()],
) -> dict[str, object]:
    """Return pending ingest request state and latest worker coverage."""
    try:
        target, state = await ingest_status(
            watchlist=watchlist,
            state_store=state_store,
            settings=runtime.settings,
            product_id=product_id,
            timeframe=timeframe,
        )
    except DataControlError as error:
        raise _http_error(error) from None
    return {
        "product_id": product_id,
        "timeframe": timeframe,
        "ingest_requested_at": (
            target.ingest_requested_at.isoformat()
            if target is not None and target.ingest_requested_at is not None
            else None
        ),
        "state": worker_state_payload(
            state,
            lookback_hours=None if target is None else target.lookback_hours,
            interval=require_interval(timeframe),
            now=datetime.now(UTC),
        ),
    }


@router.get("/gaps")
async def get_gaps(
    market_data: Annotated[MarketDataService, Depends(get_market_data_service)],
    dataset_store: Annotated[DatasetStore, Depends(get_dataset_store)],
    state_store: Annotated[MarketDataWorkerStateStore, Depends(get_market_data_state_store)],
    watchlist: Annotated[MarketDataWatchlistStore, Depends(get_market_data_watchlist_store)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    product_id: Annotated[str, Query(pattern=r"^[A-Z0-9]{2,20}-USD$")],
    timeframe: Annotated[DatasetTimeframe, Query()],
) -> dict[str, object]:
    """Classify missing bars. Does not interpolate or write Parquet."""
    try:
        starts_at, ends_at, gaps, warning = await inspect_gaps(
            service=market_data,
            dataset_store=dataset_store,
            state_store=state_store,
            watchlist=watchlist,
            settings=runtime.settings,
            product_id=product_id,
            timeframe=timeframe,
            now=datetime.now(UTC),
        )
    except DataControlError as error:
        raise _http_error(error) from None
    listed = gaps[:_MAX_LISTED_GAPS]
    payload: dict[str, object] = {
        "product_id": product_id,
        "timeframe": timeframe,
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "gap_count": len(gaps),
        "gaps": [gap_payload(item) for item in listed],
        "omitted_gap_count": max(0, len(gaps) - len(listed)),
        "interpolated": False,
    }
    if warning:
        payload["partial_result_warnings"] = [warning]
    return payload


def _http_error(error: DataControlError) -> HTTPException:
    """Map data-control failures to client or availability errors."""
    message = str(error)
    code = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if "unavailable" in message.lower()
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(status_code=code, detail=message)
