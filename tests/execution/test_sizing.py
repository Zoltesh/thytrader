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


def test_size_long_entry_reserves_fee_inside_cash() -> None:
    """Fee-adjusted cash must cap notional so notional plus fee cannot exceed cash."""
    strategy = create_reference_draft()
    payload = strategy.model_dump(mode="python")
    payload["sizing"]["max_quote_notional"] = "100000"
    payload["portfolio_limits"]["max_strategy_exposure_fraction"] = "1"
    payload["sizing"]["risk_fraction"] = "0.25"
    limited = strategy.__class__.model_validate(payload)
    sized = size_long_entry(
        strategy=limited,
        cash=Decimal("100"),
        entry_price=Decimal("100"),
        atr=Decimal("0.01"),
        product=_product(),
        fee_rate=Decimal("0.01"),
    )
    assert sized is not None
    assert sized.notional <= Decimal("100") / Decimal("1.01")

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
