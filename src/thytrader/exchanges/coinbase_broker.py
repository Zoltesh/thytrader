"""Coinbase Advanced Trade REST v3 order adapter using JSON as the source of truth."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, UUID, uuid5

from thytrader.exchanges.rest_transport import SignedHttpTransport, json_object
from thytrader.execution.broker import BrokerError, SubmitResult
from thytrader.execution.models import Fill, Order, OrderKind, OrderSide, OrderStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

    from thytrader.market_data.models import Candle

_ORDERS_PATH = "/api/v3/brokerage/orders"
_ORDER_PATH = "/api/v3/brokerage/orders/historical/{order_id}"
_LIST_ORDERS_PATH = "/api/v3/brokerage/orders/historical/batch"
_FILLS_PATH = "/api/v3/brokerage/orders/historical/fills"
_CANCEL_PATH = "/api/v3/brokerage/orders/batch_cancel"
_BOOK_PATH = "/api/v3/brokerage/product_book"
_MAX_PAGES = 20
_FILL_ID_NAMESPACE = uuid5(NAMESPACE_URL, "https://thytrader.dev/coinbase/fill")
_FILL_ORDER_NAMESPACE = uuid5(NAMESPACE_URL, "https://thytrader.dev/coinbase/fill-order")


class CoinbaseRestBroker:
    """Create, cancel, and observe spot orders through REST v3 JSON only."""

    def __init__(self, transport: SignedHttpTransport) -> None:
        """Bind the broker to a signed JSON HTTP transport."""
        self._transport = transport

    async def place_order(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        kind: OrderKind,
        quantity: Decimal,
        price: Decimal | None,
        stop_trigger_price: Decimal | None = None,
        take_profit_price: Decimal | None = None,
    ) -> SubmitResult:
        """POST /orders and map the JSON acknowledgement; GET the order if needed."""
        if not client_order_id:
            raise BrokerError("client_order_id must not be empty.")
        body: dict[str, object] = {
            "client_order_id": client_order_id,
            "product_id": product_id,
            "side": side.value.upper(),
            "order_configuration": _order_configuration(kind, quantity, price, stop_trigger_price),
        }
        attached = _attached_order_configuration(kind, take_profit_price, stop_trigger_price)
        if attached is not None:
            body["attached_order_configuration"] = attached
        try:
            payload = await asyncio.to_thread(self._transport.post, _ORDERS_PATH, body)
        except (OSError, TimeoutError, TypeError, ValueError) as error:
            raise BrokerError("Coinbase create-order request failed.") from error
        success = payload.get("success")
        order_payload = _nested_object(payload, "success_response") or _nested_object(
            payload, "order"
        )
        venue_id = _text(payload.get("order_id"))
        if order_payload is not None:
            venue_id = _text(order_payload.get("order_id")) or venue_id
        if success is False or venue_id is None:
            reason = str(payload.get("error_response") or payload.get("message") or "rejected")
            return SubmitResult(
                status=OrderStatus.REJECTED,
                venue_order_id=venue_id or client_order_id,
                reject_reason=reason[:500],
            )
        attached_child_id = None
        if order_payload is not None:
            attached_child_id = _text(order_payload.get("attached_order_id"))
        try:
            observed = await self.get_order(
                venue_order_id=venue_id,
                client_order_id=client_order_id,
            )
        except BrokerError:
            return SubmitResult(
                status=OrderStatus.UNKNOWN,
                venue_order_id=venue_id,
                reject_reason="observation_pending",
            )
        if attached_child_id is not None:
            return SubmitResult(
                status=observed.status,
                venue_order_id=observed.venue_order_id,
                filled_quantity=observed.filled_quantity,
                reject_reason=observed.reject_reason,
                fill_price=observed.fill_price,
                fill_fee=observed.fill_fee,
                attached_child_venue_order_id=attached_child_id,
            )
        return observed

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """POST batch cancel, then GET the resulting order."""
        del client_order_id
        try:
            await asyncio.to_thread(
                self._transport.post, _CANCEL_PATH, {"order_ids": [venue_order_id]}
            )
        except (OSError, TimeoutError, TypeError, ValueError) as error:
            raise BrokerError("Coinbase cancel-order request failed.") from error
        return await self.get_order(venue_order_id=venue_order_id, client_order_id=venue_order_id)

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """GET /orders/historical/{order_id}, resolving client ids when needed."""
        order_id = venue_order_id or await self._venue_id_for_client(client_order_id)
        if not order_id:
            return SubmitResult(
                status=OrderStatus.UNKNOWN,
                venue_order_id="",
                reject_reason="not_found",
            )
        try:
            payload = await asyncio.to_thread(
                self._transport.get, _ORDER_PATH.format(order_id=order_id)
            )
        except (OSError, TimeoutError, TypeError, ValueError) as error:
            raise BrokerError("Coinbase get-order request failed.") from error
        order_payload = _nested_object(payload, "order") or payload
        return _submit_from_order_json(order_payload, order_id)

    async def list_fills(
        self,
        *,
        product_id: str,
        order_id: str | None = None,
    ) -> tuple[Fill, ...]:
        """Page GET /orders/historical/fills until the documented cursor is exhausted."""
        fills: list[Fill] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _page in range(_MAX_PAGES):
            payload = await self._get_fills_page(
                product_id=product_id,
                order_id=order_id,
                cursor=cursor,
            )
            _reject_incomplete_fills_page(payload)
            fills.extend(_fills_from_page(payload, product_id=product_id, order_id=order_id))
            next_cursor = _next_fills_cursor(payload, seen_cursors)
            if next_cursor is None:
                return tuple(fills)
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise BrokerError("Coinbase fills pagination exceeded the page limit.")

    async def _get_fills_page(
        self,
        *,
        product_id: str,
        order_id: str | None,
        cursor: str | None,
    ) -> dict[str, Any]:
        """GET one List Fills page as a JSON object."""
        params = _fills_query_params(product_id=product_id, order_id=order_id, cursor=cursor)
        try:
            return await asyncio.to_thread(self._transport.get, _FILLS_PATH, params)
        except (OSError, TimeoutError, TypeError, ValueError) as error:
            raise BrokerError("Coinbase list-fills request failed.") from error

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Live fills come from REST, not candle matching."""
        del order, candle
        return None

    def maker_limit_price(
        self, *, product_id: str, mark: Decimal, side: OrderSide = OrderSide.BUY
    ) -> Decimal:
        """Rest live maker buys at the best bid and maker sells at the best ask."""
        del mark
        if side is OrderSide.SELL:
            ask = self.best_ask(product_id)
            if ask is None or ask <= 0:
                raise BrokerError("Coinbase best ask is unavailable.")
            return ask
        bid = self.best_bid(product_id)
        if bid is None or bid <= 0:
            raise BrokerError("Coinbase best bid is unavailable.")
        return bid

    def best_bid(self, product_id: str) -> Decimal | None:
        """Return the current best bid from the product book JSON."""
        payload = self._transport.get(_BOOK_PATH, {"product_id": product_id, "limit": 1})
        pricebook = payload.get("pricebook")
        mapping = pricebook if isinstance(pricebook, dict) else payload
        bids = mapping.get("bids") if isinstance(mapping, dict) else None
        if not isinstance(bids, list) or not bids:
            return None
        first = bids[0]
        if not isinstance(first, dict):
            return None
        return _decimal(first.get("price"))

    def best_ask(self, product_id: str) -> Decimal | None:
        """Return the current best ask from the product book JSON."""
        payload = self._transport.get(_BOOK_PATH, {"product_id": product_id, "limit": 1})
        pricebook = payload.get("pricebook")
        mapping = pricebook if isinstance(pricebook, dict) else payload
        asks = mapping.get("asks") if isinstance(mapping, dict) else None
        if not isinstance(asks, list) or not asks:
            return None
        first = asks[0]
        if not isinstance(first, dict):
            return None
        return _decimal(first.get("price"))

    def list_spot_orders(self, product_id: str) -> tuple[dict[str, Any], ...]:
        """Page RETAIL_ADVANCED spot orders for diagnostics; fills remain the ledger."""
        orders: list[dict[str, Any]] = []
        cursor: str | None = None
        for _page in range(_MAX_PAGES):
            params: dict[str, object] = {
                "product_ids": [product_id],
                "product_type": "SPOT",
                "order_placement_source": "RETAIL_ADVANCED",
                "limit": 100,
            }
            if cursor:
                params["cursor"] = cursor
            payload = self._transport.get(_LIST_ORDERS_PATH, params)
            orders.extend(_object_list(payload.get("orders")))
            if payload.get("has_next") is not True:
                return tuple(orders)
            next_cursor = payload.get("cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                raise BrokerError(
                    "Coinbase order pagination declared a next page without a cursor."
                )
            cursor = next_cursor
        raise BrokerError("Coinbase order pagination exceeded the page limit.")

    async def _venue_id_for_client(self, client_order_id: str) -> str | None:
        """Find the venue order id for one client order id from historical spot orders."""
        if not client_order_id:
            return None
        cursor: str | None = None
        for _page in range(_MAX_PAGES):
            params: dict[str, object] = {
                "product_type": "SPOT",
                "order_placement_source": "RETAIL_ADVANCED",
                "limit": 100,
            }
            if cursor:
                params["cursor"] = cursor
            payload = await asyncio.to_thread(self._transport.get, _LIST_ORDERS_PATH, params)
            for item in _object_list(payload.get("orders")):
                if _text(item.get("client_order_id")) == client_order_id:
                    return _text(item.get("order_id"))
            if payload.get("has_next") is not True:
                return None
            next_cursor = payload.get("cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                raise BrokerError(
                    "Coinbase order pagination declared a next page without a cursor."
                )
            cursor = next_cursor
        raise BrokerError("Coinbase order pagination exceeded the page limit.")


def _order_configuration(
    kind: OrderKind,
    quantity: Decimal,
    price: Decimal | None,
    stop_trigger_price: Decimal | None,
) -> dict[str, object]:
    """Build the Advanced Trade order_configuration object."""
    size = format(quantity, "f")
    if kind is OrderKind.MARKETABLE:
        return {"market_market_ioc": {"base_size": size}}
    if kind is OrderKind.TRIGGER_BRACKET:
        if price is None or stop_trigger_price is None:
            raise BrokerError("Trigger bracket orders require a limit and stop trigger.")
        return {
            "trigger_bracket_gtc": {
                "base_size": size,
                "limit_price": format(price, "f"),
                "stop_trigger_price": format(stop_trigger_price, "f"),
            }
        }
    if price is None:
        raise BrokerError("Post-only limit orders require a price.")
    return {
        "limit_limit_gtc": {
            "base_size": size,
            "limit_price": format(price, "f"),
            "post_only": True,
        }
    }


def _attached_order_configuration(
    kind: OrderKind,
    take_profit_price: Decimal | None,
    stop_trigger_price: Decimal | None,
) -> dict[str, object] | None:
    """Build attached TP/SL for an entry. Size is omitted; the child inherits the parent fill."""
    if kind is OrderKind.TRIGGER_BRACKET:
        return None
    if take_profit_price is None or stop_trigger_price is None:
        return None
    return {
        "trigger_bracket_gtc": {
            "limit_price": format(take_profit_price, "f"),
            "stop_trigger_price": format(stop_trigger_price, "f"),
        }
    }


def _submit_from_order_json(payload: Mapping[str, Any], fallback_id: str) -> SubmitResult:
    """Map one GET-order JSON object into a submit snapshot."""
    venue_id = _text(payload.get("order_id")) or fallback_id
    status = _status_from_coinbase(_text(payload.get("status")))
    filled = _decimal(payload.get("filled_size")) or Decimal("0")
    avg = _decimal(payload.get("average_filled_price"))
    reason = _text(payload.get("reject_reason") or payload.get("reject_message"))
    return SubmitResult(
        status=status,
        venue_order_id=venue_id,
        filled_quantity=filled,
        reject_reason=reason,
        fill_price=avg,
    )


def _status_from_coinbase(status: str | None) -> OrderStatus:
    """Map Coinbase order status strings onto the runtime enum."""
    normalized = (status or "").upper()
    mapping = {
        "PENDING": OrderStatus.PENDING,
        "OPEN": OrderStatus.OPEN,
        "FILLED": OrderStatus.FILLED,
        "CANCELLED": OrderStatus.CANCELED,
        "CANCELED": OrderStatus.CANCELED,
        "EXPIRED": OrderStatus.CANCELED,
        "FAILED": OrderStatus.REJECTED,
        "REJECTED": OrderStatus.REJECTED,
        "UNKNOWN_ORDER_STATUS": OrderStatus.UNKNOWN,
    }
    return mapping.get(normalized, OrderStatus.UNKNOWN)


def _fills_query_params(
    *,
    product_id: str,
    order_id: str | None,
    cursor: str | None,
) -> dict[str, object]:
    """Build documented List Fills query parameters for one Coinbase spot product."""
    params: dict[str, object] = {
        "product_ids": [product_id],
        "product_types": ["SPOT"],
        "limit": 100,
    }
    if order_id is not None:
        params["order_ids"] = [order_id]
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


def _nested_object(payload: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    """Return a nested JSON object, unwrapping SDK to_dict values."""
    value = payload.get(key)
    if value is None:
        return None
    try:
        return json_object(value)
    except TypeError:
        return None


def _object_list(value: object) -> tuple[dict[str, Any], ...]:
    """Narrow a JSON array to object rows."""
    if not isinstance(value, list):
        return ()
    rows: list[dict[str, Any]] = []
    for item in value:
        try:
            rows.append(json_object(item))
        except TypeError:
            continue
    return tuple(rows)


def _text(value: object) -> str | None:
    """Return a non-empty string from a JSON field."""
    if isinstance(value, str) and value:
        return value
    return None


def _decimal(value: object) -> Decimal | None:
    """Parse a JSON decimal string without binary floats."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation, ValueError:
        return None
    return parsed if parsed.is_finite() else None


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
