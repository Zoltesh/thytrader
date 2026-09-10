"""HTTP helpers for confirmation-gated market-data watchlist and ingest."""

from __future__ import annotations

from thytrader.agent_http import request_json

DATA_API_PREFIX = "/api/v1/data"
_INGEST_TIMEOUT_SECONDS = 120.0


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
    """Run one complete-only ingest through the loopback API."""
    return request_json(
        method="POST",
        url=f"{base_url}{DATA_API_PREFIX}/ingest",
        payload={"product_id": product_id, "timeframe": timeframe},
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
    """Re-run complete-only ingest for the same target as fill-gaps."""
    return ingest(base_url, product_id=product_id, timeframe=timeframe)
