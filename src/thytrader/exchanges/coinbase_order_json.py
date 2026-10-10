"""Coinbase Advanced Trade order JSON: create-order bodies and GET-order snapshots.

Shared by the REST brokers: ``order_configuration`` and attached TP/SL bodies, the GET-order
to ``SubmitResult`` mapping, and the JSON narrowing helpers the order and fill parsers use.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from thytrader.exchanges.rest_transport import json_object
from thytrader.execution.broker import BrokerError, SubmitResult
from thytrader.trading.models import OrderKind, OrderSide, OrderStatus

if TYPE_CHECKING:
    from collections.abc import Mapping


def _order_configuration(
    kind: OrderKind,
    side: OrderSide,
    quantity: Decimal,
    price: Decimal | None,
    stop_trigger_price: Decimal | None,
) -> dict[str, object]:
    """Build the Advanced Trade order_configuration object.

    ``STOP_LIMIT`` is the stop-only protective exit (ADR 0090): a sell stop triggers on
    a fall (``STOP_DIRECTION_STOP_DOWN``), a buy-to-cover stop on a rise (``STOP_UP``).
    ``ADOPTION`` is refused before any request is built: an unrecognised kind would
    otherwise fall through to a real post-only limit order (ADR 0124).
    """
    if kind is OrderKind.ADOPTION:
        raise BrokerError("Inventory adoption is never routed to the venue.")
    size = format(quantity, "f")
    if kind is OrderKind.MARKETABLE:
        return {"market_market_ioc": {"base_size": size}}
    if kind is OrderKind.STOP_LIMIT:
        if price is None or stop_trigger_price is None:
            raise BrokerError("Stop-limit orders require a limit and a stop price.")
        direction = (
            "STOP_DIRECTION_STOP_DOWN" if side is OrderSide.SELL else "STOP_DIRECTION_STOP_UP"
        )
        return {
            "stop_limit_stop_limit_gtc": {
                "base_size": size,
                "limit_price": format(price, "f"),
                "stop_price": format(stop_trigger_price, "f"),
                "stop_direction": direction,
            }
        }
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
        # Coinbase reports an entry's attached TP/SL child here (empty string when none);
        # the create response does not always carry it.
        attached_child_venue_order_id=_text(payload.get("attached_order_id")),
    )


def _status_from_coinbase(status: str | None) -> OrderStatus:
    """Map Coinbase order status strings onto the runtime enum."""
    normalized = (status or "").upper()
    mapping = {
        "PENDING": OrderStatus.PENDING,
        "QUEUED": OrderStatus.PENDING,
        "OPEN": OrderStatus.OPEN,
        # Still on the book while Coinbase processes a cancel or edit; it can still fill.
        "CANCEL_QUEUED": OrderStatus.OPEN,
        "EDIT_QUEUED": OrderStatus.OPEN,
        "FILLED": OrderStatus.FILLED,
        "CANCELLED": OrderStatus.CANCELED,
        "CANCELED": OrderStatus.CANCELED,
        "EXPIRED": OrderStatus.CANCELED,
        "FAILED": OrderStatus.REJECTED,
        "REJECTED": OrderStatus.REJECTED,
        "UNKNOWN_ORDER_STATUS": OrderStatus.UNKNOWN,
    }
    return mapping.get(normalized, OrderStatus.UNKNOWN)


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
