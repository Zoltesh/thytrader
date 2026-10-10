"""Coinbase futures (CFM) product identifiers and the data-lane market id pattern.

Futures ids look like ``BIP-20DEC30-CDE``: a 2-6 character contract code, the listed
expiry day as ``DDMONYY``, and the ``CDE`` venue suffix (Coinbase Derivatives Exchange).
The contract code is not the underlying asset: ``BIP`` is nano BTC and ``SLP`` is SOL,
so the underlying always comes from the venue catalog (``contract_root_unit``), never
from the id (ADR 0126).

``SPOT_PRODUCT_ID_PATTERN`` stays the only pattern on execution, live, adoption and
discretionary surfaces. ``MARKET_PRODUCT_ID_PATTERN`` (spot or futures) is for
read-only data surfaces only.
"""

from __future__ import annotations

from datetime import date
import re

from thytrader.market_data.products import SPOT_PRODUCT_ID_PATTERN

FUTURES_SETTLEMENT_CURRENCY = "USD"
FUTURES_PRODUCT_ID_PATTERN = r"^[A-Z0-9]{2,6}-\d{2}[A-Z]{3}\d{2}-CDE$"
# Spot or futures; built from the two anchored patterns so they cannot drift apart.
MARKET_PRODUCT_ID_PATTERN = (
    f"^(?:{SPOT_PRODUCT_ID_PATTERN[1:-1]}|{FUTURES_PRODUCT_ID_PATTERN[1:-1]})$"
)
_FUTURES_PRODUCT_ID = re.compile(FUTURES_PRODUCT_ID_PATTERN)
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_MARKET_PRODUCT_ID = re.compile(MARKET_PRODUCT_ID_PATTERN)


def is_futures_product_id(product_id: str) -> bool:
    """Return whether ``product_id`` is a Coinbase CDE futures identifier."""
    return _FUTURES_PRODUCT_ID.fullmatch(product_id.strip().upper()) is not None


def normalize_futures_product_id(product_id: str) -> str:
    """Return one uppercased futures product id or reject malformed input."""
    normalized = product_id.strip().upper()
    if not _FUTURES_PRODUCT_ID.fullmatch(normalized):
        message = "product_id must be a CODE-DDMONYY-CDE futures identifier."
        raise ValueError(message)
    return normalized


def normalize_market_product_id(product_id: str) -> str:
    """Return one uppercased spot or futures product id for read-only data surfaces."""
    normalized = product_id.strip().upper()
    if not _MARKET_PRODUCT_ID.fullmatch(normalized):
        message = (
            "product_id must be a BASE-USD, BASE-USDC, or BASE-USDT spot identifier "
            "or a CODE-DDMONYY-CDE futures identifier."
        )
        raise ValueError(message)
    return normalized


def futures_listed_expiry(product_id: str) -> date:
    """Return the expiry day encoded in a futures id (``20DEC30`` is 2030-12-20).

    Perp-style contracts report a far-future sentinel (``2089-12-30``) as their venue
    expiry, so the id date is kept as a separate, explicitly named fact.
    """
    normalized = normalize_futures_product_id(product_id)
    token = normalized.split("-")[1]
    try:
        month = _MONTHS.index(token[2:5]) + 1
        return date(2000 + int(token[5:7]), month, int(token[0:2]))
    except ValueError as error:
        message = f"Futures product id {normalized} has an invalid expiry date."
        raise ValueError(message) from error


def futures_contract_code(product_id: str) -> str:
    """Return the venue contract code (``BIP``); it is not the underlying asset."""
    return normalize_futures_product_id(product_id).split("-")[0]
