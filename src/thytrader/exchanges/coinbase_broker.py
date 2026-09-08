"""Coinbase Advanced Trade REST v3 order adapter using JSON as the source of truth."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from uuid import UUID

from thytrader.exchanges.rest_transport import SignedHttpTransport, json_object
from thytrader.execution.broker import BrokerError, SubmitResult
from thytrader.execution.ids import utc_now, uuid7
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
    ) -> SubmitResult:
        """POST /orders and map the JSON acknowledgement; GET the order if needed."""
        if not client_order_id:
            raise BrokerError("client_order_id must not be empty.")
        body: dict[str, object] = {
            "client_order_id": client_order_id,
            "product_id": product_id,
            "side": side.value.upper(),
            "order_configuration": _order_configuration(kind, quantity, price),
        }
        try:
            payload = self._transport.post(_ORDERS_PATH, body)
        except (OSError, TimeoutError, TypeError, ValueError) as error:
            raise BrokerError("Coinbase create-order request failed.") from error
        success = payload.get("success")
        order_payload = _nested_object(payload, "order")
        if success is False or order_payload is None:
            reason = str(payload.get("error_response") or payload.get("message") or "rejected")
            return SubmitResult(
                status=OrderStatus.REJECTED,
                venue_order_id=client_order_id,
                reject_reason=reason[:500],
            )
        venue_id = _text(order_payload.get("order_id")) or client_order_id
        return await self.get_order(venue_order_id=venue_id, client_order_id=client_order_id)

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """POST batch cancel, then GET the resulting order."""
        del client_order_id
        try:
            self._transport.post(_CANCEL_PATH, {"order_ids": [venue_order_id]})
        except (OSError, TimeoutError, TypeError, ValueError) as error:
            raise BrokerError("Coinbase cancel-order request failed.") from error
        return await self.get_order(venue_order_id=venue_order_id, client_order_id=venue_order_id)

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """GET /orders/historical/{order_id} as JSON."""
        del client_order_id
        try:
            payload = self._transport.get(_ORDER_PATH.format(order_id=venue_order_id))
        except (OSError, TimeoutError, TypeError, ValueError) as error:
            raise BrokerError("Coinbase get-order request failed.") from error
        order_payload = _nested_object(payload, "order") or payload
        return _submit_from_order_json(order_payload, venue_order_id)

    async def list_fills(
        self,
        *,
        product_id: str,
        order_id: str | None = None,
    ) -> tuple[Fill, ...]:
        """Page GET /orders/historical/fills until has_next is false."""
        fills: list[Fill] = []
        cursor: str | None = None
        for _page in range(_MAX_PAGES):
            params: dict[str, object] = {"product_ids": [product_id], "limit": 100}
            if order_id is not None:
                params["order_ids"] = [order_id]
            if cursor:
                params["cursor"] = cursor
            try:
                payload = self._transport.get(_FILLS_PATH, params)
            except (OSError, TimeoutError, TypeError, ValueError) as error:
                raise BrokerError("Coinbase list-fills request failed.") from error
            for item in _object_list(payload.get("fills")):
                parsed = _fill_from_json(item, product_id)
                if parsed is not None:
                    fills.append(parsed)
            if payload.get("has_next") is not True:
                return tuple(fills)
            next_cursor = payload.get("cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                raise BrokerError(
                    "Coinbase fills pagination declared a next page without a cursor."
                )
            cursor = next_cursor
        raise BrokerError("Coinbase fills pagination exceeded the page limit.")

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Live fills come from REST, not candle matching."""
        del order, candle
        return None

    def maker_limit_price(self, *, product_id: str, mark: Decimal) -> Decimal:
        """Rest live maker buys at the current best bid, not the candle close."""
        del mark
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


def _order_configuration(
    kind: OrderKind, quantity: Decimal, price: Decimal | None
) -> dict[str, object]:
    """Build the Advanced Trade order_configuration object."""
    size = format(quantity, "f")
    if kind is OrderKind.MARKETABLE:
        return {"market_market_ioc": {"base_size": size}}
    if price is None:
        raise BrokerError("Post-only limit orders require a price.")
    return {
        "limit_limit_gtc": {
            "base_size": size,
            "limit_price": format(price, "f"),
            "post_only": True,
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


def _fill_from_json(payload: Mapping[str, Any], product_id: str) -> Fill | None:
    """Parse one REST fill object into a domain fill, ignoring malformed rows."""
    del product_id
    trade_id = _text(payload.get("trade_id") or payload.get("entry_id"))
    order_id = _text(payload.get("order_id"))
    price = _decimal(payload.get("price"))
    size = _decimal(payload.get("size"))
    fee = _decimal(payload.get("commission")) or Decimal("0")
    if trade_id is None or order_id is None or price is None or size is None:
        return None
    filled_at = _timestamp(payload.get("trade_time")) or utc_now()
    synthetic_order_id = uuid7(filled_at)
    return Fill(
        id=uuid7(filled_at),
        deployment_id=UUID(int=0),
        order_id=synthetic_order_id,
        venue_fill_id=trade_id,
        price=price,
        quantity=size,
        fee=fee,
        filled_at=filled_at,
        venue_order_id=order_id,
    )


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
