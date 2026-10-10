"""Synthetic Coinbase CFM futures order/fill fixtures pin the venue facts ADR 0134 relies on.

The live futures broker (ADR 0134 P2-2) converts contracts to base-equivalent quantity, reads
the all-in fill commission, fails closed on combo and quote-sized fills, and sends exits as
``close_position``. These tests keep the fixtures it will be built against truthful: sizes are
whole-contract strings, the commission equals its itemization, single fills carry no
``future_legs``, and every id is visibly synthetic.
"""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any

import pytest

from thytrader.exchanges.coinbase_futures_catalog import parse_futures_row
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.market_data.instruments import InstrumentKind
from thytrader.trading.models import IntentPurpose

_FIXTURES = Path(__file__).parent / "fixtures"
_DIR = _FIXTURES / "coinbase_futures"
_LISTING = _FIXTURES / "coinbase_futures_listing.json"
_PERP = "ETP-20DEC30-CDE"
_CENT = Decimal("0.01")
_FIXED_PER_CONTRACT = Decimal("0.11")
_TAKER_RATE = Decimal("0.001")
_SYNTHETIC_UUID = re.compile(r"^00000000-0000-[47]000-8000-[0-9a-f]{12}$")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_ITEMIZED = (
    "client_commission",
    "venue_commission",
    "clearing_commission",
    "regulatory_commission",
)


def _body(name: str) -> dict[str, Any]:
    """Return one fixture's response body.

    JSON is the dynamic boundary here, so the body is ``dict[str, Any]`` and each test
    narrows the fields it asserts on.
    """
    payload: dict[str, Any] = json.loads((_DIR / name).read_text())
    assert payload["provenance"].startswith("Synthetic")
    return payload["body"]


def _contract_size() -> Decimal:
    """Contract size of the perp, read from the verbatim public listing via the catalog parser."""
    listing: dict[str, Any] = json.loads(_LISTING.read_text())
    row = next(row for row in listing["products"] if row["product_id"] == _PERP)
    product = parse_futures_row(row)
    assert product is not None
    assert product.kind is InstrumentKind.PERPETUAL_FUTURE
    return product.contract_size


def _whole_contracts(text: str) -> int:
    """Parse a venue size string that must be a positive whole number of contracts."""
    value = Decimal(text)
    assert value > 0
    assert value == value.to_integral_value(), f"fractional contracts: {text}"
    return int(value)


def _itemized_sum(detail: dict[str, Any]) -> Decimal:
    """Sum the rate part and the fixed parts of ``commission_detail_total``."""
    return sum((Decimal(detail[key]) for key in _ITEMIZED), Decimal(0))


def _assert_commission(total_text: str, detail: dict[str, Any], price: Decimal) -> None:
    """The all-in commission equals the itemization; the venue total is rounded to cents."""
    total = Decimal(total_text)
    assert total == _itemized_sum(detail)
    assert abs(Decimal(detail["total_commission"]) - total) <= _CENT / 2
    fixed = Decimal(detail["venue_commission"]) + Decimal(detail["clearing_commission"])
    assert fixed + Decimal(detail["regulatory_commission"]) == _FIXED_PER_CONTRACT
    assert Decimal(detail["client_commission"]) == _TAKER_RATE * price * _contract_size()
    assert detail["gst_commission"] == detail["withholding_commission"] == ""


def test_futures_orders_are_market_ioc_in_contracts() -> None:
    """Futures orders report FUTURE, MARKET/IOC, whole-contract sizes and an echoed client id."""
    orders: list[dict[str, Any]] = _body("orders_historical_batch.json")["orders"]
    contract_size = _contract_size()
    assert {order["side"] for order in orders} == {"BUY", "SELL"}
    for order in orders:
        assert order["product_id"] == _PERP
        assert order["product_type"] == "FUTURE"
        assert order["order_type"] == "MARKET"
        assert order["time_in_force"] == "IMMEDIATE_OR_CANCEL"
        assert order["status"] == "FILLED"
        assert order["size_in_quote"] is False
        assert order["is_liquidation"] is False
        assert _SYNTHETIC_UUID.match(order["client_order_id"])
        ioc = order["order_configuration"]["market_market_ioc"]
        assert isinstance(ioc["reduce_only"], bool)
        contracts = _whole_contracts(order["filled_size"])
        assert _whole_contracts(ioc["base_size"]) == contracts
        price = Decimal(order["average_filled_price"])
        assert Decimal(order["filled_value"]) == price * contracts * contract_size
        _assert_commission(order["total_fees"], order["commission_detail_total"], price)


def test_single_fills_are_contracts_with_itemized_commission() -> None:
    """Single-contract fills: FILL, contracts-denominated, no legs, commission = itemization."""
    fills: list[dict[str, Any]] = _body("orders_historical_fills.json")["fills"]
    orders = {o["order_id"]: o for o in _body("orders_historical_batch.json")["orders"]}
    assert len(fills) == len(orders)
    for fill in fills:
        assert fill["trade_type"] == "FILL"
        assert fill["product_type"] == "FUTURE"
        assert fill["future_legs"] == []
        assert fill["option_legs"] == []
        assert fill["size_in_quote"] is False
        assert fill["realized_pl"] == ""
        order = orders[fill["order_id"]]
        assert fill["side"] == order["side"]
        assert _whole_contracts(fill["size"]) == _whole_contracts(order["filled_size"])
        assert fill["commission"] == order["total_fees"]
        _assert_commission(
            fill["commission"], fill["commission_detail_total"], Decimal(fill["price"])
        )


def test_rounded_total_commission_is_not_the_exact_fee() -> None:
    """At least one fixture fill shows the venue's cent-rounded total differing from the fee."""
    fills: list[dict[str, Any]] = _body("orders_historical_fills.json")["fills"]
    assert any(
        Decimal(fill["commission_detail_total"]["total_commission"]) != Decimal(fill["commission"])
        for fill in fills
    )


def test_flat_cfm_positions_are_an_empty_list() -> None:
    """A flat account reports an empty positions list, not a zero-size row."""
    assert _body("cfm_positions_flat.json") == {"positions": []}


def test_combo_fill_carries_future_legs() -> None:
    """A combo fill has non-empty legs sharing one combo id; the broker must fail closed on it."""
    (fill,) = _body("fill_combo_future_legs.json")["fills"]
    legs: list[dict[str, Any]] = fill["future_legs"]
    assert len(legs) >= 2
    assert len({leg["combo_id"] for leg in legs}) == 1
    for leg in legs:
        assert set(leg) == {
            "product_id",
            "combo_id",
            "trade_id",
            "filled_price",
            "filled_size",
            "side",
        }
        assert is_futures_product_id(leg["product_id"])
        _whole_contracts(leg["filled_size"])


def test_size_in_quote_fill_is_flagged() -> None:
    """A quote-sized fill sets ``size_in_quote``; its size is not a contract count."""
    (fill,) = _body("fill_size_in_quote.json")["fills"]
    assert fill["size_in_quote"] is True
    assert fill["future_legs"] == []


def test_close_position_success_echoes_the_thytrader_client_id() -> None:
    """A close_position success names the order and echoes a ThyTrader-format client id."""
    body = _body("close_position_success.json")
    assert body["success"] is True
    assert "error_response" not in body
    success: dict[str, Any] = body["success_response"]
    assert set(success) == {"order_id", "product_id", "side", "client_order_id"}
    assert success["product_id"] == _PERP
    deployment, purpose, stamp, intent = success["client_order_id"].split(":")
    assert _SYNTHETIC_UUID.match(deployment)
    assert IntentPurpose(purpose) is IntentPurpose.SIGNAL_EXIT
    assert re.fullmatch(r"\d{8}T\d{4}", stamp)
    assert _SYNTHETIC_UUID.match(intent)


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("close_position_failure.json", "CANNOT_CLOSE_ZERO_POSITION"),
        ("create_order_fcm_session_rejected.json", "INVALID_FCM_TRADING_SESSION"),
    ],
)
def test_definite_rejections_name_a_failure_reason(name: str, reason: str) -> None:
    """A refused create or close is ``success: false`` with a ``new_order_failure_reason``."""
    body = _body(name)
    assert body["success"] is False
    assert "success_response" not in body
    error: dict[str, Any] = body["error_response"]
    assert error["new_order_failure_reason"] == reason
    assert error["error"] == reason


def test_fcm_session_rejection_was_a_post_only_limit() -> None:
    """The session rejection fixture is the entry shape P2-2 sends: post-only GTC limit."""
    body = _body("create_order_fcm_session_rejected.json")
    limit = body["order_configuration"]["limit_limit_gtc"]
    assert limit["post_only"] is True
    _whole_contracts(limit["base_size"])


@pytest.mark.parametrize("path", sorted(_DIR.glob("*.json")), ids=lambda path: path.name)
def test_every_id_in_the_fixtures_is_synthetic(path: Path) -> None:
    """Every UUID is visibly fake and every product id is a CDE futures id."""
    text = path.read_text()
    for uuid in _UUID.findall(text):
        assert _SYNTHETIC_UUID.match(uuid), f"{path.name}: {uuid} is not synthetic"
    for product_id in re.findall(r'"product_id": "([^"]+)"', text):
        assert is_futures_product_id(product_id)
