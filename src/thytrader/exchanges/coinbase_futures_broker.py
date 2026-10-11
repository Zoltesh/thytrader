"""Dormant CFM adapter; no worker or API constructs this broker (ADR 0134)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from thytrader.exchanges.coinbase_broker import (
    _DEFINITE_CREATE_REJECTIONS,
    _cancel_failure_reason,
    _http_reject_reason,
)
from thytrader.exchanges.coinbase_fills import _next_fills_cursor, _reject_incomplete_fills_page
from thytrader.exchanges.coinbase_futures_fills import futures_fills_from_page, venue_decimal
from thytrader.exchanges.coinbase_futures_units import (
    FuturesReductionMode,
    request_refusal,
    require_contract_size,
    to_base,
    to_contracts,
)
from thytrader.exchanges.coinbase_order_identity import (
    order_identity,
    read_order,
    scan_client_order,
)
from thytrader.exchanges.coinbase_order_json import (
    _nested_object,
    _order_configuration,
    _status_from_coinbase,
    _text,
)
from thytrader.exchanges.rest_transport import CoinbaseHttpStatusError
from thytrader.execution.broker import CANCEL_PENDING_REASON, BrokerError, SubmitResult
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.trading.models import Fill, OrderKind, OrderSide, OrderStatus

if TYPE_CHECKING:
    from collections.abc import Mapping
    from decimal import Decimal

    from thytrader.exchanges.rest_transport import SignedHttpTransport
    from thytrader.execution.broker import ContractSizeSource
    from thytrader.market_data.models import Candle
    from thytrader.trading.models import Order

_ORDERS_PATH = "/api/v3/brokerage/orders"
_CLOSE_PATH = "/api/v3/brokerage/orders/close_position"
_CANCEL_PATH = "/api/v3/brokerage/orders/batch_cancel"
_FILLS_PATH = "/api/v3/brokerage/orders/historical/fills"
_MAX_PAGES = 20
_TERMINAL = frozenset({OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED})


class CoinbaseFuturesBroker:
    """Convert base units exactly; constructor modes are candidates, not venue proof."""

    def __init__(
        self,
        transport: SignedHttpTransport,
        contract_sizes: ContractSizeSource,
        *,
        reduction_mode: FuturesReductionMode = FuturesReductionMode.CLOSE_POSITION,
        cancel_confirm_delay_seconds: float = 0.5,
    ) -> None:
        """Bind explicit dormant mode; neither requests nor automatic fallback occur here."""
        if not isinstance(reduction_mode, FuturesReductionMode):
            raise TypeError("An explicit FuturesReductionMode is required.")
        self._transport = transport
        self._contract_sizes = contract_sizes
        self._reduction_mode = reduction_mode
        self._cancel_confirm_delay = cancel_confirm_delay_seconds

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
        """Validate before sending; marketable intents must be identified reductions."""
        refusal = request_refusal(
            client_order_id=client_order_id,
            product_id=product_id,
            side=side,
            kind=kind,
            price=price,
            stop_trigger_price=stop_trigger_price,
            take_profit_price=take_profit_price,
        )
        if refusal is not None:
            return SubmitResult(OrderStatus.REJECTED, client_order_id, reject_reason=refusal)
        try:
            size = await require_contract_size(self._contract_sizes, product_id)
            contracts = to_contracts(quantity, size)
        except BrokerError as error:
            return SubmitResult(OrderStatus.REJECTED, client_order_id, reject_reason=str(error))
        body: dict[str, object] = {"client_order_id": client_order_id, "product_id": product_id}
        path = _ORDERS_PATH
        if (
            kind is OrderKind.MARKETABLE
            and self._reduction_mode is FuturesReductionMode.CLOSE_POSITION
        ):
            path = _CLOSE_PATH
            body["size"] = format(contracts, "f")
        else:
            body["side"] = side.value.upper()
            body["order_configuration"] = (
                {"market_market_ioc": {"base_size": format(contracts, "f"), "reduce_only": True}}
                if kind is OrderKind.MARKETABLE
                else _order_configuration(kind, side, contracts, price, stop_trigger_price)
            )
        # Only this validated method can select a create/reduction request. No public
        # submit-body/market helper bypasses the intent-purpose gate above.
        try:
            payload = await asyncio.to_thread(self._transport.post, path, body)
        except CoinbaseHttpStatusError as error:
            if error.status_code in _DEFINITE_CREATE_REJECTIONS:
                return SubmitResult(
                    OrderStatus.REJECTED, client_order_id, reject_reason=_http_reject_reason(error)
                )
            raise BrokerError("Coinbase futures submit is ambiguous.") from error
        except (OSError, TimeoutError, TypeError, ValueError) as error:
            raise BrokerError("Coinbase futures submit is ambiguous.") from error
        return await self._observe_ack(payload, client_order_id, product_id)

    async def _observe_ack(
        self,
        payload: Mapping[str, Any],
        client_order_id: str,
        product_id: str,
    ) -> SubmitResult:
        """Narrow create JSON immediately; malformed success is ambiguous, not a refusal."""
        row = _nested_object(payload, "success_response") or _nested_object(payload, "order") or {}
        venue_id = _text(row.get("order_id")) or _text(payload.get("order_id"))
        if payload.get("success") is False and venue_id is None:
            error = _nested_object(payload, "error_response") or {}
            reason = _text(error.get("error")) or "venue_rejected"
            return SubmitResult(OrderStatus.REJECTED, client_order_id, reject_reason=reason[:500])
        if payload.get("success") is not True or venue_id is None:
            return SubmitResult(
                OrderStatus.UNKNOWN, venue_id or "", reject_reason="acknowledgement_unknown"
            )
        try:
            return await self._observe_order(venue_id, client_order_id, product_id)
        except BrokerError:
            return SubmitResult(OrderStatus.UNKNOWN, venue_id, reject_reason="observation_pending")

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Resolve a client-only identity with FUTURE pages, then observe exact base units."""
        if not venue_order_id:
            identity = await scan_client_order(
                self._transport,
                client_order_id=client_order_id,
                params={"product_type": "FUTURE", "limit": 100},
            )
            if identity is None:
                return SubmitResult(OrderStatus.UNKNOWN, "", reject_reason="not_found")
            return await self._observe_order(
                identity.venue_order_id, client_order_id, identity.product_id
            )
        return await self._observe_order(venue_order_id, client_order_id)

    async def _observe_order(
        self,
        venue_order_id: str,
        client_order_id: str,
        product_id: str | None = None,
    ) -> SubmitResult:
        """Reject identity, quantity and price anomalies before returning financial evidence."""
        row = await read_order(
            self._transport, venue_order_id=venue_order_id, client_order_id=client_order_id
        )
        identity = order_identity(row)
        if row["product_type"] != "FUTURE" or (
            product_id is not None and identity.product_id != product_id
        ):
            raise BrokerError("Coinbase futures order product mismatch.")
        if row.get("size_in_quote") is not False:
            raise BrokerError("Coinbase futures order is not contract-sized.")
        size = await require_contract_size(self._contract_sizes, identity.product_id)
        contracts = venue_decimal(row.get("filled_size"), field="filled_size", allow_zero=True)
        quantity = to_base(contracts, size, allow_zero=True)
        status = _status_from_coinbase(_text(row.get("status")))
        if status is OrderStatus.FILLED and contracts == 0:
            raise BrokerError("Coinbase filled order has no filled contracts.")
        price = None
        if contracts > 0:
            price = venue_decimal(row.get("average_filled_price"), field="average_filled_price")
        return SubmitResult(
            status=status,
            venue_order_id=venue_order_id,
            filled_quantity=quantity,
            fill_price=price,
            reject_reason=_text(row.get("reject_reason") or row.get("reject_message")),
            attached_child_venue_order_id=_text(row.get("attached_order_id")),
        )

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Verify futures identity before cancel, then bound confirmation to three reads."""
        initial = await self.get_order(
            venue_order_id=venue_order_id, client_order_id=client_order_id
        )
        if not initial.venue_order_id:
            raise BrokerError("Cannot cancel an unresolved futures order.")
        if initial.status in _TERMINAL:
            return initial
        venue_order_id = initial.venue_order_id
        try:
            payload = await asyncio.to_thread(
                self._transport.post, _CANCEL_PATH, {"order_ids": [venue_order_id]}
            )
        except (OSError, TimeoutError, TypeError, ValueError) as error:
            raise BrokerError("Coinbase futures cancel request failed.") from error
        failure = _cancel_failure_reason(payload, venue_order_id)
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(self._cancel_confirm_delay)
            observed = await self.get_order(
                venue_order_id=venue_order_id, client_order_id=client_order_id
            )
            if observed.status in _TERMINAL:
                return observed
        reason = (
            CANCEL_PENDING_REASON
            if failure in {None, "DUPLICATE_CANCEL_REQUEST"}
            else f"cancel_failed:{failure}"
        )
        return replace(observed, reject_reason=reason[:500])

    async def find_order_by_client_id(
        self,
        *,
        client_order_id: str,
        product_id: str,
        submitted_at: datetime,
    ) -> SubmitResult | None:
        """Scan FUTURE orders from the submit window; incomplete evidence raises."""
        await require_contract_size(self._contract_sizes, product_id)
        if submitted_at.tzinfo is None:
            raise BrokerError("Client lookup requires an aware submit timestamp.")
        try:
            start = (submitted_at - timedelta(minutes=5)).astimezone(UTC)
        except (OverflowError, ValueError) as error:
            raise BrokerError("Invalid client lookup time window.") from error
        identity = await scan_client_order(
            self._transport,
            client_order_id=client_order_id,
            product_id=product_id,
            params={
                "product_type": "FUTURE",
                "product_ids": [product_id],
                "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": 100,
            },
        )
        if identity is None:
            return None
        return await self._observe_order(identity.venue_order_id, client_order_id, product_id)

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Live fills are venue observations, never fabricated from candle matching."""
        del order, candle
        return None

    def maker_limit_price(
        self,
        *,
        product_id: str,
        mark: Decimal,
        side: OrderSide = OrderSide.BUY,
    ) -> Decimal:
        """Buy at the best bid or sell at the best ask; a missing book never uses mark."""
        del mark
        price = self.best_ask(product_id) if side is OrderSide.SELL else self.best_bid(product_id)
        if price is None:
            raise BrokerError("Coinbase futures maker price unavailable.")
        return price

    def best_bid(self, product_id: str) -> Decimal | None:
        """Return a validated positive best bid or None for an empty book."""
        return self._book_price(product_id, "bids")

    def best_ask(self, product_id: str) -> Decimal | None:
        """Return a validated positive best ask or None for an empty book."""
        return self._book_price(product_id, "asks")

    def _book_price(self, product_id: str, side: str) -> Decimal | None:
        """Narrow book JSON before using a quote; wrong-product data is never authority."""
        if not is_futures_product_id(product_id):
            raise BrokerError("FUTURES_PRODUCT_UNSUPPORTED")
        try:
            payload = self._transport.get(
                "/api/v3/brokerage/product_book", {"product_id": product_id, "limit": 1}
            )
        except (OSError, TimeoutError, TypeError, ValueError) as error:
            raise BrokerError("Coinbase futures book read failed.") from error
        book = _nested_object(payload, "pricebook")
        if book is None or book.get("product_id") != product_id:
            raise BrokerError("Coinbase futures book product is unknown.")
        rows = book.get(side)
        if rows == []:
            return None
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise BrokerError("Coinbase futures book is malformed.")
        return venue_decimal(rows[0].get("price"), field="book price")

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Read bounded FUTURE pages; incomplete evidence raises rather than skipping fills."""
        size = await require_contract_size(self._contract_sizes, product_id)
        params: dict[str, object] = (
            {"order_ids": [order_id], "limit": 100}
            if order_id is not None
            else {"product_ids": [product_id], "product_types": ["FUTURE"], "limit": 100}
        )
        fills: list[Fill] = []
        seen: set[str] = set()
        for _page in range(_MAX_PAGES):
            try:
                payload = await asyncio.to_thread(self._transport.get, _FILLS_PATH, params.copy())
            except (OSError, TimeoutError, TypeError, ValueError) as error:
                raise BrokerError("Coinbase futures fills request failed.") from error
            _reject_incomplete_fills_page(payload)
            fills.extend(
                futures_fills_from_page(
                    payload, product_id=product_id, order_id=order_id, contract_size=size
                )
            )
            if payload.get("cursor") is not None and not isinstance(payload["cursor"], str):
                raise BrokerError("Invalid futures fills cursor.")
            cursor = _next_fills_cursor(payload, seen)
            if cursor is None:
                return tuple(fills)
            seen.add(cursor)
            params["cursor"] = cursor
        raise BrokerError("Coinbase futures fills pagination exceeded the page limit.")
