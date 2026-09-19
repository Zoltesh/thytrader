"""Coinbase spot product identifiers and supported quote-currency helpers."""

from __future__ import annotations

import re
from typing import Literal

SpotQuoteCurrency = Literal["USD", "USDC", "USDT"]
SPOT_QUOTE_CURRENCIES: tuple[SpotQuoteCurrency, ...] = ("USD", "USDC", "USDT")
DEFAULT_SPOT_QUOTE_CURRENCY: SpotQuoteCurrency = "USDC"
SPOT_PRODUCT_ID_PATTERN = r"^[A-Z0-9]{2,20}-(?:USD|USDC|USDT)$"
_SPOT_PRODUCT_ID = re.compile(SPOT_PRODUCT_ID_PATTERN)


def normalize_spot_product_id(product_id: str) -> str:
    """Return one uppercased spot product id or reject malformed input."""
    normalized = product_id.strip().upper()
    if not _SPOT_PRODUCT_ID.fullmatch(normalized):
        message = "product_id must be a BASE-USD, BASE-USDC, or BASE-USDT spot identifier."
        raise ValueError(message)
    return normalized


def parse_spot_product_id(product_id: str) -> tuple[str, SpotQuoteCurrency]:
    """Parse base and quote currencies from one spot product id."""
    normalized = normalize_spot_product_id(product_id)
    base, quote = normalized.rsplit("-", 1)
    if quote == "USD":
        return base, "USD"
    if quote == "USDC":
        return base, "USDC"
    if quote == "USDT":
        return base, "USDT"
    message = "product_id must be a BASE-USD, BASE-USDC, or BASE-USDT spot identifier."
    raise ValueError(message)


def base_currency(product_id: str) -> str:
    """Return the base currency from one spot product id."""
    return parse_spot_product_id(product_id)[0]


def quote_currency(product_id: str) -> SpotQuoteCurrency:
    """Return the quote currency from one spot product id."""
    return parse_spot_product_id(product_id)[1]


def is_spot_product_id(product_id: str) -> bool:
    """Return whether ``product_id`` matches the supported spot pattern."""
    return _SPOT_PRODUCT_ID.fullmatch(product_id.strip().upper()) is not None


def default_spot_product_id(*, quote: SpotQuoteCurrency = DEFAULT_SPOT_QUOTE_CURRENCY) -> str:
    """Return the conservative default BTC product for one quote currency."""
    return f"BTC-{quote}"
