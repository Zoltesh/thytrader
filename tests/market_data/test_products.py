"""Tests for shared USD and USDC spot product helpers."""

from __future__ import annotations

import pytest

from thytrader.market_data.products import (
    base_currency,
    is_spot_product_id,
    normalize_spot_product_id,
    parse_spot_product_id,
    quote_currency,
)
from thytrader.strategies.models import Instrument


def test_spot_product_pattern_accepts_usd_usdc_and_usdt() -> None:
    """USD, USDC, and USDT quote suffixes must pass the shared product regex."""
    assert is_spot_product_id("BTC-USD")
    assert is_spot_product_id("btc-usdc")
    assert is_spot_product_id("ETH-USDT")
    assert not is_spot_product_id("BTC-EUR")


def test_parse_spot_product_id_returns_normalized_components() -> None:
    """Parsing uppercases ids and splits base and quote currencies."""
    assert parse_spot_product_id(" eth-usdc ") == ("ETH", "USDC")
    assert parse_spot_product_id("btc-usdt") == ("BTC", "USDT")
    assert base_currency("SOL-USD") == "SOL"
    assert quote_currency("SOL-USDC") == "USDC"
    assert quote_currency("ETH-USDT") == "USDT"
    assert normalize_spot_product_id("btc-usdc") == "BTC-USDC"


def test_instrument_accepts_usdc_product() -> None:
    """Strategy instruments must accept USDC-quoted spot products."""
    instrument = Instrument(
        product_id="BTC-USDC",
        base_currency="BTC",
        quote_currency="USDC",
    )
    assert instrument.product_id == "BTC-USDC"


def test_instrument_rejects_mismatched_quote() -> None:
    """Product components must stay internally consistent."""
    with pytest.raises(ValueError, match="product_id must match"):
        Instrument(
            product_id="BTC-USD",
            base_currency="BTC",
            quote_currency="USDC",
        )
