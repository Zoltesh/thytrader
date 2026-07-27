"""Coinbase Advanced Trade read-only account adapter."""

from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from requests import HTTPError

from thytrader.exchanges.models import ExchangeBalance

_ACCOUNT_PAGE_SIZE = 250
_MAX_ACCOUNT_PAGES = 100


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


class CoinbaseAccount:
    """Expose Coinbase account data through the provider-neutral contract."""

    def __init__(self, client: CoinbaseClient) -> None:
        """Initialize the adapter around an authenticated official SDK client."""
        self._client = client

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Fetch every account page and return active, non-empty balances."""
        cursor: str | None = None
        seen_cursors: set[str] = set()
        balances: list[ExchangeBalance] = []
        for _page_number in range(_MAX_ACCOUNT_PAGES):
            response = await asyncio.to_thread(
                self._client.get_accounts,
                limit=_ACCOUNT_PAGE_SIZE,
                cursor=cursor,
            )
            payload = response.to_dict()
            for account in self._account_items(payload):
                balance = self._parse_balance(account)
                if balance is not None and balance.total != 0:
                    balances.append(balance)
            if not payload.get("has_next"):
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
        response = await asyncio.to_thread(self._client.get_api_key_permissions)
        payload = response.to_dict()
        permission_fields = (
            ("can_view", "view"),
            ("can_trade", "trade"),
            ("can_transfer", "transfer"),
        )
        return tuple(label for field, label in permission_fields if payload.get(field) is True)

    async def get_usd_price(self, currency: str) -> Decimal | None:
        """Return the latest direct USD product price when Coinbase exposes one."""
        try:
            response = await asyncio.to_thread(self._client.get_product, f"{currency}-USD")
        except HTTPError as error:
            if error.response is not None and error.response.status_code == 404:
                return None
            raise
        price = response.to_dict().get("price")
        if not isinstance(price, str):
            return None
        try:
            return Decimal(price)
        except InvalidOperation:
            return None

    @staticmethod
    def _account_items(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        """Narrow untrusted account payloads to dictionary entries."""
        accounts = payload.get("accounts")
        if not isinstance(accounts, list):
            return ()
        return tuple(account for account in accounts if isinstance(account, dict))

    @staticmethod
    def _parse_balance(account: dict[str, Any]) -> ExchangeBalance | None:
        """Validate one SDK account payload into an exact domain balance."""
        currency = account.get("currency")
        if not isinstance(currency, str) or not currency:
            return None
        available = CoinbaseAccount._amount_value(account.get("available_balance"))
        hold = CoinbaseAccount._amount_value(account.get("hold"))
        if available is None or hold is None:
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
            return Decimal(raw)
        except InvalidOperation:
            return None
