"""Bounded, read-only Coinbase order identity resolution for opaque venue ids."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from thytrader.execution.broker import BrokerError, VenueOrderIdentity
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.market_data.products import is_spot_product_id

if TYPE_CHECKING:
    from collections.abc import Mapping

    from thytrader.exchanges.rest_transport import SignedHttpTransport

_ORDER_PATH = "/api/v3/brokerage/orders/historical/"
_LIST_PATH = "/api/v3/brokerage/orders/historical/batch"
_MAX_PAGES = 20


async def read_order(
    transport: SignedHttpTransport,
    *,
    venue_order_id: str,
    client_order_id: str = "",
) -> dict[str, Any]:
    """Validate identity at the JSON boundary before any unit-dependent interpretation."""
    if not venue_order_id:
        raise BrokerError("Order identity is required.")
    try:
        payload = await asyncio.to_thread(
            transport.get, _ORDER_PATH + quote(venue_order_id, safe="")
        )
    except (OSError, TimeoutError, TypeError, ValueError) as error:
        raise BrokerError("Coinbase order identity read failed.") from error
    row = payload.get("order")
    if not isinstance(row, dict):
        raise BrokerError("Coinbase order is not an object.")
    identity = order_identity(row)
    if identity.venue_order_id != venue_order_id or (
        client_order_id and row.get("client_order_id") != client_order_id
    ):
        raise BrokerError("Coinbase order identity mismatch.")
    return row


def order_identity(row: Mapping[str, Any]) -> VenueOrderIdentity:
    """Only a consistent explicit product id/type may determine a broker route."""
    product = row.get("product_id")
    venue_id = row.get("order_id")
    if not isinstance(product, str) or not isinstance(venue_id, str) or not venue_id:
        raise BrokerError("Coinbase order identity is incomplete.")
    kind = row.get("product_type")
    if not (
        (kind == "SPOT" and is_spot_product_id(product))
        or (kind == "FUTURE" and is_futures_product_id(product))
    ):
        raise BrokerError("Coinbase order product type is inconsistent.")
    return VenueOrderIdentity(venue_order_id=venue_id, product_id=product)


async def scan_client_order(
    transport: SignedHttpTransport,
    *,
    client_order_id: str,
    params: Mapping[str, object],
    product_id: str | None = None,
) -> VenueOrderIdentity | None:
    """Complete at most twenty pages; no cache, id heuristic, or incomplete absence claim.

    Any is limited to external JSON, checked here before becoming a named domain identity.
    A matching client id on multiple orders is ambiguous even across different products.
    """
    if not client_order_id:
        raise BrokerError("Client order identity is required.")
    query = dict(params)
    seen: set[str] = set()
    found: VenueOrderIdentity | None = None
    for _page in range(_MAX_PAGES):
        try:
            payload = await asyncio.to_thread(transport.get, _LIST_PATH, query.copy())
        except (OSError, TimeoutError, TypeError, ValueError) as error:
            raise BrokerError("Coinbase client identity scan failed.") from error
        candidate = _identity_on_page(payload, client_order_id, product_id)
        if candidate is not None:
            if found is not None and found != candidate:
                raise BrokerError("Coinbase client order identity is ambiguous.")
            found = candidate
        cursor = _next_order_cursor(payload, seen)
        if cursor is None:
            return found
        seen.add(cursor)
        query["cursor"] = cursor
    raise BrokerError("Coinbase order identity scan exceeded the page limit.")


def _identity_on_page(
    payload: Mapping[str, Any],
    client_order_id: str,
    product_id: str | None,
) -> VenueOrderIdentity | None:
    """Reject malformed rows and wrong-product or duplicate matching client identities."""
    rows = payload.get("orders")
    if not isinstance(rows, list):
        raise BrokerError("Coinbase orders page is incomplete.")
    found: VenueOrderIdentity | None = None
    for row in rows:
        if not isinstance(row, dict):
            raise BrokerError("Coinbase order row is malformed.")
        if row.get("client_order_id") != client_order_id:
            continue
        candidate = order_identity(row)
        if product_id is not None and candidate.product_id != product_id:
            raise BrokerError("Coinbase client order product mismatch.")
        if found is not None and found != candidate:
            raise BrokerError("Coinbase client order identity is ambiguous.")
        found = candidate
    return found


def _next_order_cursor(payload: Mapping[str, Any], seen: set[str]) -> str | None:
    """A missing completion flag or repeated cursor cannot establish scan completeness."""
    if payload.get("has_next") is False:
        return None
    cursor = payload.get("cursor")
    if (
        payload.get("has_next") is not True
        or not isinstance(cursor, str)
        or not cursor
        or cursor in seen
    ):
        raise BrokerError("Coinbase order pagination is incomplete.")
    return cursor


async def resolve_order_identity(
    transport: SignedHttpTransport,
    *,
    venue_order_id: str,
    client_order_id: str,
) -> VenueOrderIdentity | None:
    """Read the venue's product, or bounded-scan all products when only client id is known."""
    if venue_order_id:
        return order_identity(
            await read_order(
                transport, venue_order_id=venue_order_id, client_order_id=client_order_id
            )
        )
    return await scan_client_order(
        transport, client_order_id=client_order_id, params={"limit": 100}
    )
