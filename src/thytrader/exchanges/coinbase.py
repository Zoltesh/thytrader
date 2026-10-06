"""Coinbase Advanced Trade read-only account adapter."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import partial
import logging
import threading
from typing import TYPE_CHECKING, Any, Protocol

from requests import HTTPError, RequestException, Timeout

from thytrader.exchanges.fees import FeeProfile
from thytrader.exchanges.models import ExchangeBalance, ExchangeOpenOrder
from thytrader.exchanges.read_errors import (
    ExchangeReadError,
    ExchangeReadFailure,
    ExchangeReadFailureKind,
    ExchangeReadOperation,
)
from thytrader.exchanges.rest_transport import http_status_error, json_object

if TYPE_CHECKING:
    from collections.abc import Callable

_ACCOUNT_PAGE_SIZE = 250
_MAX_ACCOUNT_PAGES = 100
_ORDER_PAGE_SIZE = 100
_MAX_ORDER_PAGES = 100
_SDK_LOGGER_NAME = "coinbase.RESTClient"
_logger = logging.getLogger(__name__)

UNSUPPORTED_USD_PRODUCTS: set[str] = set()
"""Products Coinbase answered 404 for in this process (a dust asset with no USD market).

Valuation asks Coinbase once per product per process; later portfolio reads skip the
call and report the asset in ``unvalued_assets`` without another 404.
"""
_EXPECTED_NOT_FOUND = threading.local()


class _ExpectedNotFoundFilter(logging.Filter):
    """Drop the SDK's own ERROR line for a product 404 this adapter expects and handles."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Keep every record except an expected product-lookup 404."""
        expected = bool(getattr(_EXPECTED_NOT_FOUND, "active", False))
        return not (expected and record.getMessage().startswith("HTTP Error: 404"))


_SDK_NOT_FOUND_FILTER = _ExpectedNotFoundFilter()


class CoinbasePaginationError(RuntimeError):
    """Signal malformed Coinbase account pagination without returning partial balances."""


class CoinbaseResponse(Protocol):
    """Minimal response behavior used from the official Coinbase SDK."""

    def to_dict(self) -> dict[str, Any]:
        """Convert an SDK response to plain Python values."""
        ...


class CoinbaseClient(Protocol):
    """Subset of the official Coinbase REST client required for portfolios."""

    def get_accounts(self, *, limit: int, cursor: str | None = None) -> CoinbaseResponse:
        """Return one page of account balances."""
        ...

    def get_api_key_permissions(self) -> CoinbaseResponse:
        """Return permissions assigned to the configured key."""
        ...

    def get_product(self, product_id: str) -> CoinbaseResponse:
        """Return one product including its latest price."""
        ...

    def get_transaction_summary(self, **kwargs: Any) -> CoinbaseResponse:
        """Return 30-day volume and fee tier summary."""
        ...

    def list_orders(self, **kwargs: Any) -> CoinbaseResponse:
        """Return one page of historical orders for the given filters."""
        ...


class CoinbaseAccount:
    """Expose Coinbase account data through the provider-neutral contract."""

    def __init__(
        self, client: CoinbaseClient, *, unsupported_products: set[str] | None = None
    ) -> None:
        """Initialize the adapter around an authenticated official SDK client.

        ``unsupported_products`` defaults to the process-wide cache of products that have
        no USD market (:data:`UNSUPPORTED_USD_PRODUCTS`).
        """
        self._client = client
        self._unsupported = (
            UNSUPPORTED_USD_PRODUCTS if unsupported_products is None else unsupported_products
        )
        sdk_logger = logging.getLogger(_SDK_LOGGER_NAME)
        if _SDK_NOT_FOUND_FILTER not in sdk_logger.filters:
            sdk_logger.addFilter(_SDK_NOT_FOUND_FILTER)

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Fetch every account page and return active, non-empty balances."""
        cursor: str | None = None
        seen_cursors: set[str] = set()
        balances: list[ExchangeBalance] = []
        for _page_number in range(_MAX_ACCOUNT_PAGES):
            payload = await self._read(
                ExchangeReadOperation.BALANCES,
                partial(self._client.get_accounts, limit=_ACCOUNT_PAGE_SIZE, cursor=cursor),
            )
            for account in self._account_items(payload):
                balance = self._parse_balance(account)
                if balance is None:
                    raise _invalid_listing(ExchangeReadOperation.BALANCES)
                if balance.total != 0:
                    balances.append(balance)
            has_next = payload.get("has_next")
            if not isinstance(has_next, bool):
                raise _invalid_listing(ExchangeReadOperation.BALANCES)
            if not has_next:
                return tuple(balances)
            next_cursor = payload.get("cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                message = "Coinbase account pagination declared a next page with a missing cursor."
                raise CoinbasePaginationError(message)
            if next_cursor in seen_cursors:
                message = "Coinbase account pagination returned a repeated cursor."
                raise CoinbasePaginationError(message)
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        message = f"Coinbase account pagination exceeded the {_MAX_ACCOUNT_PAGES}-page limit."
        raise CoinbasePaginationError(message)

    async def get_permissions(self) -> tuple[str, ...]:
        """Report every enabled permission without rejecting additional capabilities."""
        payload = await self._read(
            ExchangeReadOperation.PERMISSIONS, self._client.get_api_key_permissions
        )
        permission_fields = (
            ("can_view", "view"),
            ("can_trade", "trade"),
            ("can_transfer", "transfer"),
        )
        return tuple(label for field, label in permission_fields if payload.get(field) is True)

    async def get_usd_price(self, currency: str) -> Decimal | None:
        """Return the latest direct USD product price when Coinbase exposes one.

        A 404 (no ``<currency>-USD`` market, e.g. a dust asset) is cached for the process
        and logged once at INFO; the asset is then reported as unvalued without asking
        Coinbase again. Every other failure propagates.
        """
        product_id = f"{currency}-USD"
        if product_id in self._unsupported:
            return None
        try:
            payload = await self._read(
                ExchangeReadOperation.PRICE, partial(self._get_product_expecting_404, product_id)
            )
        except ExchangeReadError as error:
            if error.failure.http_status == 404:
                self._unsupported.add(product_id)
                _logger.info(
                    "Coinbase has no %s market; %s is reported in unvalued_assets "
                    "(not requested again by this process).",
                    product_id,
                    currency,
                )
                return None
            raise
        price = payload.get("price")
        if not isinstance(price, str):
            return None
        try:
            return Decimal(price)
        except InvalidOperation:
            return None

    def _get_product_expecting_404(self, product_id: str) -> CoinbaseResponse:
        """Fetch one product while the SDK's ERROR line for a 404 is suppressed."""
        _EXPECTED_NOT_FOUND.active = True
        try:
            return self._client.get_product(product_id)
        finally:
            _EXPECTED_NOT_FOUND.active = False

    async def get_fee_profile(self) -> FeeProfile:
        """Fetch 30-day volume and fee tier details from Coinbase."""
        payload = await self._read(ExchangeReadOperation.FEES, self._client.get_transaction_summary)
        return self._parse_fee_profile(payload)

    async def list_open_orders(self) -> tuple[ExchangeOpenOrder, ...]:
        """Page spot order history and retain every recognized nonterminal order.

        No status/time/source/account filter: OPEN-only hides unresolved CANCEL_QUEUED.
        OPEN, PENDING, QUEUED, CANCEL_QUEUED and EDIT_QUEUED remain working; known
        terminal orders are omitted only after validation. Malformed rows/pages, unknown
        statuses, duplicate order IDs, cursor cycles and page exhaustion fail closed.
        This is a sequential REST observation, not an atomic venue snapshot.
        """
        cursor: str | None = None
        seen_cursors: set[str] = set()
        orders: list[ExchangeOpenOrder] = []
        seen_orders: set[str] = set()
        for _page_number in range(_MAX_ORDER_PAGES):
            payload = await self._read(
                ExchangeReadOperation.OPEN_ORDERS,
                partial(
                    self._client.list_orders,
                    product_type="SPOT",
                    limit=_ORDER_PAGE_SIZE,
                    cursor=cursor,
                ),
            )
            page = _open_orders_from_page(payload)
            for order in page:
                if order.venue_order_id in seen_orders:
                    raise _invalid_listing(ExchangeReadOperation.OPEN_ORDERS)
                seen_orders.add(order.venue_order_id)
                if order.status in _NONTERMINAL_ORDER_STATUSES:
                    orders.append(order)
            has_next = payload.get("has_next")
            if not isinstance(has_next, bool):
                raise _invalid_listing(ExchangeReadOperation.OPEN_ORDERS)
            if not has_next:
                return tuple(orders)
            next_cursor = payload.get("cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                raise CoinbasePaginationError(
                    "Coinbase order pagination declared a next page with a missing cursor."
                )
            if next_cursor in seen_cursors:
                raise CoinbasePaginationError(
                    "Coinbase order pagination returned a repeated cursor."
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        message = f"Coinbase order pagination exceeded the {_MAX_ORDER_PAGES}-page limit."
        raise CoinbasePaginationError(message)

    async def _read(
        self, operation: ExchangeReadOperation, call: Callable[[], CoinbaseResponse]
    ) -> dict[str, Any]:
        """Retry one transient read once, with fresh signing and the same page cursor.

        Timeout/network and HTTP 502/503/504 may recover after a 0.5-second wait.
        Authentication, rate limits, malformed responses and pagination never retry.
        Only this read-only account adapter uses the helper; order writes do not.
        """
        try:
            return await self._read_once(operation, call)
        except ExchangeReadError as error:
            failure = error.failure
            retryable = failure.kind in {
                ExchangeReadFailureKind.TIMEOUT,
                ExchangeReadFailureKind.NETWORK,
            } or (
                failure.kind is ExchangeReadFailureKind.HTTP
                and failure.http_status in {502, 503, 504}
            )
            if not retryable:
                raise
        await asyncio.sleep(0.5)
        try:
            return await self._read_once(operation, call)
        except ExchangeReadError as error:
            raise ExchangeReadError(error.failure.model_copy(update={"attempts": 2})) from None

    async def _read_once(
        self, operation: ExchangeReadOperation, call: Callable[[], CoinbaseResponse]
    ) -> dict[str, Any]:
        """Narrow SDK JSON and replace request errors with safe, typed read evidence.

        ``Any`` is confined to the SDK JSON boundary; the existing parsers validate
        its fields before returning domain values. This helper issues only reads.
        """
        try:
            response = await asyncio.to_thread(call)
            return json_object(response)
        except HTTPError as error:
            status_error = http_status_error(error)
            failure = ExchangeReadFailure(
                operation=operation,
                kind=ExchangeReadFailureKind.HTTP,
                http_status=None if status_error is None else status_error.status_code,
            )
        except Timeout:
            failure = ExchangeReadFailure(operation=operation, kind=ExchangeReadFailureKind.TIMEOUT)
        except ValueError, TypeError:
            failure = ExchangeReadFailure(
                operation=operation, kind=ExchangeReadFailureKind.INVALID_RESPONSE
            )
        except RequestException, OSError:
            failure = ExchangeReadFailure(operation=operation, kind=ExchangeReadFailureKind.NETWORK)
        raise ExchangeReadError(failure) from None

    def _parse_fee_profile(self, payload: dict[str, Any]) -> FeeProfile:
        """Map one complete Coinbase transaction summary into exact fee evidence."""
        fee_tier_raw = payload.get("fee_tier")
        if isinstance(fee_tier_raw, dict):
            fee_tier_dict = fee_tier_raw
        elif fee_tier_raw is not None and hasattr(fee_tier_raw, "to_dict"):
            fee_tier_dict = fee_tier_raw.to_dict()
            if not isinstance(fee_tier_dict, dict):
                raise TypeError("Coinbase fee tier response must be a mapping.")
        else:
            raise ValueError("Coinbase fee tier response is missing.")

        pricing_tier = fee_tier_dict.get("pricing_tier")
        taker_rate_raw = fee_tier_dict.get("taker_fee_rate")
        maker_rate_raw = fee_tier_dict.get("maker_fee_rate")
        total_volume_raw = payload.get("total_volume")
        if not isinstance(pricing_tier, str) or not pricing_tier.strip():
            raise ValueError("Coinbase fee tier name is missing.")
        if taker_rate_raw is None or maker_rate_raw is None or total_volume_raw is None:
            raise ValueError("Coinbase fee rate or 30d volume is missing.")
        if any(
            isinstance(value, bool) for value in (taker_rate_raw, maker_rate_raw, total_volume_raw)
        ):
            raise ValueError("Coinbase fee values must not be booleans.")

        try:
            taker_rate = Decimal(str(taker_rate_raw))
            maker_rate = Decimal(str(maker_rate_raw))
            total_volume = Decimal(str(total_volume_raw))
        except InvalidOperation as err:
            raise ValueError("Coinbase fee response contains an invalid decimal.") from err
        if not all(value.is_finite() for value in (taker_rate, maker_rate, total_volume)):
            raise ValueError("Coinbase fee response contains a non-finite decimal.")

        return FeeProfile(
            taker_fee_rate=taker_rate,
            maker_fee_rate=maker_rate,
            usd_volume_30d=total_volume,
            fee_tier=pricing_tier,
            as_of=datetime.now(UTC),
            source="coinbase",
        )

    @staticmethod
    def _account_items(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        """Narrow untrusted account payloads to dictionary entries."""
        accounts = payload.get("accounts")
        if not isinstance(accounts, list) or any(
            not isinstance(account, dict) for account in accounts
        ):
            raise _invalid_listing(ExchangeReadOperation.BALANCES)
        return tuple(accounts)

    @staticmethod
    def _parse_balance(account: dict[str, Any]) -> ExchangeBalance | None:
        """Validate one SDK account payload into an exact domain balance."""
        currency = account.get("currency")
        if not isinstance(currency, str) or not currency:
            return None
        available = CoinbaseAccount._amount_value(account.get("available_balance"))
        hold = CoinbaseAccount._amount_value(account.get("hold"))
        if available is None or hold is None or available < 0 or hold < 0:
            return None
        name = account.get("name")
        return ExchangeBalance(
            currency=currency,
            name=name if isinstance(name, str) and name else currency,
            available=available,
            hold=hold,
        )

    @staticmethod
    def _amount_value(value: object) -> Decimal | None:
        """Parse one Coinbase amount mapping without binary floating point."""
        if not isinstance(value, dict):
            return None
        raw = value.get("value")
        if not isinstance(raw, str):
            return None
        try:
            amount = Decimal(raw)
            return amount if amount.is_finite() else None
        except InvalidOperation:
            return None


_NONTERMINAL_ORDER_STATUSES = frozenset(
    {"OPEN", "PENDING", "QUEUED", "CANCEL_QUEUED", "EDIT_QUEUED"}
)
_TERMINAL_ORDER_STATUSES = frozenset(
    {"FILLED", "CANCELLED", "CANCELED", "EXPIRED", "FAILED", "REJECTED"}
)


def _invalid_listing(operation: ExchangeReadOperation) -> ExchangeReadError:
    """Return redacted invalid-response evidence instead of a guessed complete listing."""
    return ExchangeReadError(
        ExchangeReadFailure(
            operation=operation,
            kind=ExchangeReadFailureKind.INVALID_RESPONSE,
        )
    )


def _open_orders_from_page(payload: dict[str, Any]) -> tuple[ExchangeOpenOrder, ...]:
    """Validate every historical spot order row; malformed rows invalidate the page.

    ``Any`` is confined to SDK JSON here. Unknown status is not a terminal order;
    dropping an unidentified or malformed row would fabricate complete coverage.
    """
    items = payload.get("orders")
    if not isinstance(items, list):
        raise _invalid_listing(ExchangeReadOperation.OPEN_ORDERS)
    rows: list[ExchangeOpenOrder] = []
    for item in items:
        if not isinstance(item, dict):
            raise _invalid_listing(ExchangeReadOperation.OPEN_ORDERS)
        venue_order_id = _plain_text(item.get("order_id"))
        product = _plain_text(item.get("product_id"))
        side = _plain_text(item.get("side"))
        status = _plain_text(item.get("status"))
        if (
            venue_order_id is None
            or product is None
            or len(product.split("-")) != 2
            or any(not part for part in product.split("-"))
            or side not in {"BUY", "SELL"}
            or status not in _NONTERMINAL_ORDER_STATUSES | _TERMINAL_ORDER_STATUSES
        ):
            raise _invalid_listing(ExchangeReadOperation.OPEN_ORDERS)
        client_id = item.get("client_order_id")
        if client_id is not None and not isinstance(client_id, str):
            raise _invalid_listing(ExchangeReadOperation.OPEN_ORDERS)
        rows.append(
            ExchangeOpenOrder(
                venue_order_id=venue_order_id,
                product_id=product,
                side=side.lower(),
                status=status,
                client_order_id=_plain_text(item.get("client_order_id")),
            )
        )
    return tuple(rows)


def _plain_text(value: object) -> str | None:
    """Return one non-empty plain string field, or None."""
    return value if isinstance(value, str) and value.strip() and value == value.strip() else None
