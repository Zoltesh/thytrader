"""Strict CFM fills: exact base units, all-in commission, no combo allocation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid5

from thytrader.exchanges.coinbase_fills import (
    _FILL_ID_NAMESPACE,
    _FILL_ORDER_NAMESPACE,
    _fill_identity,
    _require_fill_order_id,
    _require_fill_product,
    _require_trade_time,
)
from thytrader.exchanges.coinbase_futures_units import to_base
from thytrader.execution.broker import BrokerError
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.trading.models import Fill

if TYPE_CHECKING:
    from collections.abc import Mapping


def venue_decimal(value: object, *, field: str, allow_zero: bool = False) -> Decimal:
    """Narrow untrusted JSON strings/integers, never binary floats or booleans."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise BrokerError(f"Invalid futures {field}.")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise BrokerError(f"Invalid futures {field}.") from error
    if not parsed.is_finite() or parsed < 0 or (parsed == 0 and not allow_zero):
        raise BrokerError(f"Invalid futures {field}.")
    return parsed


def futures_fills_from_page(
    payload: Mapping[str, Any],
    *,
    product_id: str,
    order_id: str | None,
    contract_size: Decimal,
) -> tuple[Fill, ...]:
    """Validate every dynamic JSON row immediately; malformed rows quarantine the page."""
    rows = payload.get("fills")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise BrokerError("Invalid futures fills page.")
    return tuple(
        _futures_fill_from_json(
            row, product_id=product_id, order_id=order_id, contract_size=contract_size
        )
        for row in rows
    )


def _futures_fill_from_json(
    payload: Mapping[str, Any],
    *,
    product_id: str,
    order_id: str | None,
    contract_size: Decimal,
) -> Fill:
    """Require single-product FUTURE execution evidence and preserve stable fill identities."""
    _require_fill_product(payload, product_id)
    if not is_futures_product_id(product_id) or payload.get("product_type") != "FUTURE":
        raise BrokerError("Not a futures fill.")
    if payload.get("trade_type") != "FILL" or payload.get("size_in_quote") is not False:
        raise BrokerError("Not a base-sized futures execution.")
    if payload.get("future_legs") != [] or payload.get("option_legs", []) != []:
        raise BrokerError("Combo or unknown futures legs are unsupported.")
    venue_id = _fill_identity(payload)
    venue_order = _require_fill_order_id(payload, expected_order_id=order_id)
    quantity = to_base(venue_decimal(payload.get("size"), field="size"), contract_size)
    price = venue_decimal(payload.get("price"), field="price")
    fee = venue_decimal(payload.get("commission"), field="commission", allow_zero=True)
    _require_aware_time(payload.get("trade_time"))
    return Fill(
        id=uuid5(_FILL_ID_NAMESPACE, venue_id),
        deployment_id=UUID(int=0),
        order_id=uuid5(_FILL_ORDER_NAMESPACE, venue_order),
        venue_fill_id=venue_id,
        price=price,
        quantity=quantity,
        fee=fee,
        filled_at=_require_trade_time(payload),
        venue_order_id=venue_order,
    )


def _require_aware_time(value: object) -> None:
    """Reject missing offsets instead of manufacturing venue timestamp provenance."""
    if not isinstance(value, str):
        raise BrokerError("Invalid futures trade timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise BrokerError("Invalid futures trade timestamp.") from error
    if parsed.tzinfo is None:
        raise BrokerError("Futures trade timestamp has no timezone.")
