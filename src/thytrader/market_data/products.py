"""Coinbase spot product identifiers and supported quote-currency helpers."""

from __future__ import annotations

import re
from typing import Literal

SpotQuoteCurrency = Literal["USD", "USDC"]
SPOT_QUOTE_CURRENCIES: tuple[SpotQuoteCurrency, ...] = ("USD", "USDC")
SPOT_PRODUCT_ID_PATTERN = r"^[A-Z0-9]{2,20}-(?:USD|USDC)$"
_SPOT_PRODUCT_ID = re.compile(SPOT_PRODUCT_ID_PATTERN)


def normalize_spot_product_id(product_id: str) -> str:
    """Return one uppercased spot product id or reject malformed input."""
    normalized = product_id.strip().upper()
    if not _SPOT_PRODUCT_ID.fullmatch(normalized):
        message = "product_id must be a BASE-USD or BASE-USDC spot identifier."
        raise ValueError(message)
    return normalized


def parse_spot_product_id(product_id: str) -> tuple[str, SpotQuoteCurrency]:
    """Parse base and quote currencies from one spot product id."""
    normalized = normalize_spot_product_id(product_id)
    base, quote = normalized.rsplit("-", 1)
    return base, quote  # type: ignore[return-value]


def base_currency(product_id: str) -> str:
    """Return the base currency from one spot product id."""
    return parse_spot_product_id(product_id)[0]


def quote_currency(product_id: str) -> SpotQuoteCurrency:
    """Return the quote currency from one spot product id."""
    return parse_spot_product_id(product_id)[1]


def is_spot_product_id(product_id: str) -> bool:
    """Return whether ``product_id`` matches the supported spot pattern."""
    return _SPOT_PRODUCT_ID.fullmatch(product_id.strip().upper()) is not None
