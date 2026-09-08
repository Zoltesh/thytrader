"""ATR risk-fraction sizing quantized to venue increments."""

from decimal import Decimal

from thytrader.execution.sizing import size_long_entry
from thytrader.market_data.models import MarketProduct
from thytrader.strategies.authoring import create_reference_draft


def _product() -> MarketProduct:
    """Return BTC-USD increments used by Coinbase spot."""
    return MarketProduct(
        product_id="BTC-USD",
        base_currency="BTC",
        quote_currency="USD",
        price_increment=Decimal("0.01"),
        base_increment=Decimal("0.00000001"),
        quote_increment=Decimal("0.01"),
        base_min_size=Decimal("0.0001"),
        quote_min_size=Decimal("1"),
        trading_enabled=True,
    )


def test_size_long_entry_respects_risk_fraction_and_increments() -> None:
    """Quantity snaps down to the base increment and stay inside quote bounds."""
    strategy = create_reference_draft()
    sized = size_long_entry(
        strategy=strategy,
        cash=Decimal("10000"),
        entry_price=Decimal("100"),
        atr=Decimal("1"),
        product=_product(),
    )

    assert sized is not None
    assert sized.entry_price == Decimal("100.00")
    assert sized.stop_price == Decimal("98.00")
    assert sized.target_price == Decimal("104.00")
    assert sized.notional <= Decimal("100")
    assert sized.notional == sized.quantity * sized.entry_price


def test_size_long_entry_rejects_insufficient_cash() -> None:
    """Below the minimum quote notional there is no executable size."""
    strategy = create_reference_draft()
    sized = size_long_entry(
        strategy=strategy,
        cash=Decimal("1"),
        entry_price=Decimal("100"),
        atr=Decimal("1"),
        product=_product(),
    )
    assert sized is None
