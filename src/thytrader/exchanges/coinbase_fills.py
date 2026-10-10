"""Coinbase Advanced Trade List Fills parsing: query params, pagination, fail-closed fills."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, UUID, uuid5

from thytrader.exchanges.coinbase_order_json import _decimal, _text
from thytrader.exchanges.rest_transport import json_object
from thytrader.execution.broker import BrokerError
from thytrader.trading.models import Fill

if TYPE_CHECKING:
    from collections.abc import Mapping
    from decimal import Decimal

_FILL_ID_NAMESPACE = uuid5(NAMESPACE_URL, "https://thytrader.dev/coinbase/fill")
_FILL_ORDER_NAMESPACE = uuid5(NAMESPACE_URL, "https://thytrader.dev/coinbase/fill-order")


def _fills_query_params(
    *,
    product_id: str,
    order_id: str | None,
    cursor: str | None,
) -> dict[str, object]:
    """Build documented List Fills query parameters for one Coinbase spot product.

    Coinbase rejects ``order_ids`` combined with any other filter (HTTP 400 "cannot pass
    order_ids with other filters"), so an order-scoped query sends only ``order_ids``.
    Every returned fill is still checked locally for the expected order, product, and spot
    trade type, failing closed on any mismatch.
    """
    params: dict[str, object]
    if order_id is not None:
        params = {"order_ids": [order_id], "limit": 100}
    else:
        params = {"product_ids": [product_id], "product_types": ["SPOT"], "limit": 100}
    if cursor:
        params["cursor"] = cursor
    return params


def _reject_incomplete_fills_page(payload: Mapping[str, Any]) -> None:
    """Fail closed when Coinbase withholds fill history behind a proof token."""
    if payload.get("proof_token_required") is True:
        raise BrokerError(
            "Coinbase fills pagination requires a proof token; the ledger is incomplete."
        )


def _next_fills_cursor(payload: Mapping[str, Any], seen_cursors: set[str]) -> str | None:
    """Return the next List Fills cursor, or None when the documented stream ends.

    GetFillsResponse documents ``cursor``, not ``has_next``. An empty or missing cursor
    ends the page stream. A repeated cursor, or ``has_next: true`` without a cursor, is
    incomplete evidence and must not look like a complete ledger.
    """
    raw_cursor = payload.get("cursor")
    cursor = raw_cursor if isinstance(raw_cursor, str) and raw_cursor else None
    if payload.get("has_next") is True and cursor is None:
        raise BrokerError("Coinbase fills pagination declared a next page without a cursor.")
    if cursor is None:
        return None
    if cursor in seen_cursors:
        raise BrokerError("Coinbase fills pagination returned a repeated cursor.")
    return cursor


def _fills_from_page(
    payload: Mapping[str, Any],
    *,
    product_id: str,
    order_id: str | None,
) -> tuple[Fill, ...]:
    """Parse every fill on one page; non-object rows quarantine the ledger."""
    raw = payload.get("fills")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise BrokerError("Coinbase fills response is not a list.")
    fills: list[Fill] = []
    for item in raw:
        try:
            mapping = json_object(item)
        except TypeError as error:
            raise BrokerError("Coinbase fill row is not a JSON object.") from error
        fills.append(_fill_from_json(mapping, product_id=product_id, order_id=order_id))
    return tuple(fills)


def _fill_from_json(
    payload: Mapping[str, Any],
    *,
    product_id: str,
    order_id: str | None,
) -> Fill:
    """Parse one REST fill into a domain fill, or raise if financial evidence is incomplete."""
    venue_fill_id = _fill_identity(payload)
    venue_order_id = _require_fill_order_id(payload, expected_order_id=order_id)
    _require_fill_product(payload, product_id)
    _reject_non_spot_fill(payload)
    price = _require_positive_decimal(payload.get("price"), field="price")
    quantity = _base_fill_quantity(payload, price)
    fee = _require_fill_fee(payload)
    filled_at = _require_trade_time(payload)
    return Fill(
        id=uuid5(_FILL_ID_NAMESPACE, venue_fill_id),
        deployment_id=UUID(int=0),
        order_id=uuid5(_FILL_ORDER_NAMESPACE, venue_order_id),
        venue_fill_id=venue_fill_id,
        price=price,
        quantity=quantity,
        fee=fee,
        filled_at=filled_at,
        venue_order_id=venue_order_id,
    )


def _fill_identity(payload: Mapping[str, Any]) -> str:
    """Return the documented fill identity, preferring trade_id then entry_id."""
    identity = _text(payload.get("trade_id")) or _text(payload.get("entry_id"))
    if identity is None:
        raise BrokerError("Coinbase fill is missing trade_id and entry_id.")
    return identity


def _require_fill_order_id(payload: Mapping[str, Any], *, expected_order_id: str | None) -> str:
    """Require a venue order id and verify optional order membership."""
    reported = _text(payload.get("order_id"))
    if reported is None:
        raise BrokerError("Coinbase fill order_id is missing.")
    if expected_order_id is not None and reported != expected_order_id:
        raise BrokerError("Coinbase fill order_id does not match the requested order.")
    return reported


def _require_fill_product(payload: Mapping[str, Any], product_id: str) -> None:
    """Require the fill's product_id to match the requested Coinbase spot product."""
    reported = _text(payload.get("product_id"))
    if reported is None:
        raise BrokerError("Coinbase fill product_id is missing.")
    if reported != product_id:
        raise BrokerError("Coinbase fill product_id does not match the requested product.")


def _reject_non_spot_fill(payload: Mapping[str, Any]) -> None:
    """Reject adjusted, synthetic, or futures-leg fills as unparseable spot evidence."""
    trade_type = payload.get("trade_type")
    if trade_type is not None:
        normalized = _text(trade_type)
        if normalized is None or normalized.upper() != "FILL":
            raise BrokerError("Coinbase fill trade_type is not a spot FILL execution.")
    legs = payload.get("future_legs")
    if isinstance(legs, list) and legs:
        raise BrokerError("Coinbase fill includes futures legs; spot fills only.")


def _base_fill_quantity(payload: Mapping[str, Any], price: Decimal) -> Decimal:
    """Convert size to base units when Coinbase reports quote-sized fills."""
    size = _require_positive_decimal(payload.get("size"), field="size")
    size_in_quote = payload.get("size_in_quote")
    if size_in_quote is None or size_in_quote is False:
        return size
    if size_in_quote is not True:
        raise BrokerError("Coinbase fill size_in_quote is not a boolean.")
    quantity = size / price
    if not quantity.is_finite() or quantity <= 0:
        raise BrokerError(
            "Coinbase quote-sized fill did not convert to a finite positive base quantity."
        )
    return quantity


def _require_positive_decimal(value: object, *, field: str) -> Decimal:
    """Parse a finite Decimal strictly greater than zero."""
    parsed = _decimal(value)
    if parsed is None or parsed <= 0:
        raise BrokerError(f"Coinbase fill {field} is missing or not a finite positive decimal.")
    return parsed


def _require_fill_fee(payload: Mapping[str, Any]) -> Decimal:
    """Require an explicit finite non-negative commission; do not invent zero."""
    if "commission" not in payload:
        raise BrokerError("Coinbase fill commission is missing.")
    parsed = _decimal(payload.get("commission"))
    if parsed is None or parsed < 0:
        raise BrokerError(
            "Coinbase fill commission is missing or not a finite non-negative decimal."
        )
    return parsed


def _require_trade_time(payload: Mapping[str, Any]) -> datetime:
    """Require RFC3339 trade_time; never substitute local observation time."""
    filled_at = _timestamp(payload.get("trade_time"))
    if filled_at is None:
        raise BrokerError("Coinbase fill trade_time is missing or not a UTC timestamp.")
    return filled_at


def _timestamp(value: object) -> datetime | None:
    """Parse an RFC3339 timestamp as UTC."""
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
