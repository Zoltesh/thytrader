"""HTTP helpers for confirmation-gated market-data watchlist and ingest."""

from __future__ import annotations

import time

from thytrader.agent_http import request_json
from thytrader.data_control.models import DataControlError

DATA_API_PREFIX = "/api/v1/data"
_INGEST_TIMEOUT_SECONDS = 120.0
_INGEST_POLL_SECONDS = 1.0


def list_watchlist(base_url: str) -> object:
    """Return the durable ingestion watchlist."""
    return request_json(method="GET", url=f"{base_url}{DATA_API_PREFIX}/watchlist")


def add_watch(
    base_url: str,
    *,
    product_id: str,
    timeframe: str,
    lookback_hours: int,
    enabled: bool,
) -> object:
    """Upsert one watch target through the loopback API."""
    return request_json(
        method="PUT",
        url=f"{base_url}{DATA_API_PREFIX}/watchlist",
        payload={
            "product_id": product_id,
            "timeframe": timeframe,
            "lookback_hours": lookback_hours,
            "enabled": enabled,
        },
    )


def ingest(
    base_url: str,
    *,
    product_id: str,
    timeframe: str,
) -> object:
    """Queue ingest and poll until the market-data worker finishes or times out."""
    request_json(
        method="POST",
        url=f"{base_url}{DATA_API_PREFIX}/ingest",
        payload={"product_id": product_id, "timeframe": timeframe},
        timeout=_INGEST_TIMEOUT_SECONDS,
    )
    deadline = time.monotonic() + _INGEST_TIMEOUT_SECONDS
    while True:
        payload = ingest_status(base_url, product_id=product_id, timeframe=timeframe)
        if not _ingest_pending(payload):
            return payload
        if time.monotonic() >= deadline:
            raise DataControlError(
                "Timed out waiting for the market-data worker to finish ingest. "
                "Confirm thytrader-market-data-worker is running."
            )
        remaining = deadline - time.monotonic()
        time.sleep(min(_INGEST_POLL_SECONDS, max(0.0, remaining)))


def ingest_status(
    base_url: str,
    *,
    product_id: str,
    timeframe: str,
) -> object:
    """Return pending ingest request state and latest worker coverage."""
    return request_json(
        method="GET",
        url=(f"{base_url}{DATA_API_PREFIX}/ingest?product_id={product_id}&timeframe={timeframe}"),
        timeout=_INGEST_TIMEOUT_SECONDS,
    )


def inspect_gaps(
    base_url: str,
    *,
    product_id: str,
    timeframe: str,
) -> object:
    """Classify missing bars without writing datasets."""
    return request_json(
        method="GET",
        url=(f"{base_url}{DATA_API_PREFIX}/gaps?product_id={product_id}&timeframe={timeframe}"),
        timeout=_INGEST_TIMEOUT_SECONDS,
    )


def fill_gaps(
    base_url: str,
    *,
    product_id: str,
    timeframe: str,
) -> object:
    """Re-queue complete-only ingest for the same target as fill-gaps."""
    return ingest(base_url, product_id=product_id, timeframe=timeframe)


def _ingest_pending(payload: object) -> bool:
    """True while the worker has not yet consumed the ingest request."""
    if not isinstance(payload, dict):
        return False
    return payload.get("ingest_requested_at") is not None
