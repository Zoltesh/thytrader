"""Explicit Coinbase tradability beats aliases regardless of provider row order."""

import pytest

from thytrader.exchanges.coinbase_market_data import _parse_products


@pytest.mark.parametrize("reverse", [False, True])
def test_disabled_explicit_row_cannot_be_reenabled_by_an_alias(reverse: bool) -> None:
    """An enabled USD source never overrides a disabled USDC row's exact constraints."""
    enabled = {
        "product_id": "BTC-USD",
        "base_currency_id": "BTC",
        "quote_currency_id": "USD",
        "price_increment": "0.01",
        "base_increment": "0.00000001",
        "quote_increment": "0.01",
        "base_min_size": "0.0001",
        "quote_min_size": "1",
        "is_disabled": False,
        "trading_disabled": False,
        "alias_to": ["BTC-USDC"],
    }
    disabled = {
        **enabled,
        "product_id": "BTC-USDC",
        "quote_currency_id": "USDC",
        "is_disabled": True,
        "alias_to": [],
        "quote_min_size": "5",
    }
    rows = [disabled, enabled] if reverse else [enabled, disabled]
    products = _parse_products({"products": rows})
    assert [product.product_id for product in products] == ["BTC-USD", "BTC-USDC"]
    assert products[0].trading_enabled
    assert not products[1].trading_enabled
    assert str(products[1].quote_min_size) == "5"
