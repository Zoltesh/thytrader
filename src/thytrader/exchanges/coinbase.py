"""Coinbase Advanced Trade read-only account adapter."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from requests import HTTPError

from thytrader.exchanges.fees import FeeProfile
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

    def get_transaction_summary(self, **kwargs: Any) -> CoinbaseResponse:
        """Return 30-day volume and fee tier summary."""
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

    async def get_fee_profile(self) -> FeeProfile:
        """Fetch 30-day volume and fee tier details from Coinbase."""
        response = await asyncio.to_thread(self._client.get_transaction_summary)
        payload = response.to_dict()
        return self._parse_fee_profile(payload)

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
