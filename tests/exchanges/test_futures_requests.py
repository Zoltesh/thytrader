"""CFM request safety: constructor-selected reductions and no-I/O refusals."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import TYPE_CHECKING, Any

import pytest

from tests.exchanges.test_coinbase_broker import FakeTransport
from tests.exchanges.test_coinbase_futures_broker import (
    CLIENT,
    HISTORICAL,
    ORDERS,
    PRODUCT,
    Sizes,
    order_json,
)
from thytrader.exchanges import coinbase_futures_broker as module
from thytrader.trading.models import IntentPurpose, OrderKind, OrderSide, OrderStatus

if TYPE_CHECKING:
    from thytrader.execution.broker import SubmitResult


async def place(
    transport: FakeTransport, *, size: Decimal | None = Decimal("0.1"), **changes: Any
) -> SubmitResult:
    """Exercise public submission with dynamic boundary overrides only in tests."""
    args: dict[str, Any] = {
        "client_order_id": CLIENT,
        "product_id": PRODUCT,
        "side": OrderSide.SELL,
        "kind": OrderKind.POST_ONLY_LIMIT,
        "quantity": Decimal("0.2"),
        "price": Decimal("2500"),
    }
    args.update(changes)
    return await module.CoinbaseFuturesBroker(transport, Sizes(size)).place_order(**args)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"product_id": "BTC-USD"}, "FUTURES_PRODUCT_UNSUPPORTED"),
        ({"client_order_id": ""}, "FUTURES_INTENT_INVALID"),
        ({"client_order_id": "opaque"}, "FUTURES_INTENT_INVALID"),
        ({"size": None}, "FUTURES_CONTRACT_SIZE_UNKNOWN"),
        ({"size": Decimal("sNaN")}, "FUTURES_CONTRACT_SIZE_UNKNOWN"),
        ({"size": Decimal("0")}, "FUTURES_CONTRACT_SIZE_UNKNOWN"),
        ({"quantity": Decimal("0.15")}, "FUTURES_FRACTIONAL_CONTRACTS"),
        ({"quantity": Decimal("0")}, "FUTURES_FRACTIONAL_CONTRACTS"),
        ({"quantity": Decimal("NaN")}, "FUTURES_FRACTIONAL_CONTRACTS"),
        ({"quantity": Decimal("Infinity")}, "FUTURES_FRACTIONAL_CONTRACTS"),
        (
            {"quantity": Decimal("0.1000000000000000000000000000000001")},
            "FUTURES_FRACTIONAL_CONTRACTS",
        ),
        ({"kind": OrderKind.MARKETABLE}, "FUTURES_ORDER_KIND_UNSUPPORTED"),
        ({"kind": OrderKind.ADOPTION}, "FUTURES_ORDER_KIND_UNSUPPORTED"),
        ({"kind": OrderKind.STOP_LIMIT}, "FUTURES_ORDER_KIND_UNSUPPORTED"),
        ({"price": None}, "FUTURES_PRICE_INVALID"),
        ({"price": Decimal("sNaN")}, "FUTURES_PRICE_INVALID"),
        ({"price": Decimal("-1")}, "FUTURES_PRICE_INVALID"),
        ({"take_profit_price": Decimal("2600")}, "FUTURES_ATTACHMENT_UNVERIFIED"),
        ({"stop_trigger_price": Decimal("2400")}, "FUTURES_ATTACHMENT_UNVERIFIED"),
    ],
)
async def test_pre_request_refusal_sends_nothing(changes: dict[str, Any], reason: str) -> None:
    """Invalid data is a definite refusal, never UNKNOWN and never a transport send."""
    transport = FakeTransport(gets={})
    result = await place(transport, **changes)
    assert result.status is OrderStatus.REJECTED
    assert result.reject_reason == reason
    assert transport.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("disabled_traps", [False, True])
async def test_fractional_quantity_cannot_round_into_a_contract(disabled_traps: bool) -> None:
    """Caller precision/traps cannot manufacture whole-contract evidence."""
    transport = FakeTransport(gets={})
    with localcontext() as context:
        context.prec = 2
        if disabled_traps:
            context.clear_traps()
        result = await place(transport, quantity=Decimal("0.101"))
    assert result.status is OrderStatus.REJECTED
    assert transport.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("side", list(OrderSide))
@pytest.mark.parametrize("mode", ["close_position", "reduce_only_market"])
@pytest.mark.parametrize(
    "purpose",
    [
        IntentPurpose.STOP,
        IntentPurpose.TIME_EXIT,
        IntentPurpose.SIGNAL_EXIT,
        IntentPurpose.LIQUIDATION,
        IntentPurpose.TAKE_PROFIT,
        IntentPurpose.BRACKET,
    ],
)
async def test_explicit_reduction_body(side: OrderSide, mode: str, purpose: IntentPurpose) -> None:
    """Both candidate modes are fake-tested, not claimed accepted by CFM."""
    assert hasattr(module, "FuturesReductionMode")
    client = CLIENT.replace(":entry:", f":{purpose.value}:")
    path = ORDERS + "/close_position" if mode == "close_position" else ORDERS
    transport = FakeTransport(
        posts={path: [{"success": True, "success_response": {"order_id": "venue-test"}}]},
        gets={HISTORICAL: [{"order": order_json(client_order_id=client)}]},
    )
    broker = module.CoinbaseFuturesBroker(
        transport, Sizes(), reduction_mode=module.FuturesReductionMode(mode)
    )
    result = await broker.place_order(
        client_order_id=client,
        product_id=PRODUCT,
        side=side,
        kind=OrderKind.MARKETABLE,
        quantity=Decimal("0.2"),
        price=None,
    )
    expected: dict[str, object] = {"client_order_id": client, "product_id": PRODUCT}
    if mode == "close_position":
        expected["size"] = "2"
    else:
        expected.update(
            side=side.value.upper(),
            order_configuration={
                "market_market_ioc": {
                    "base_size": "2",
                    "reduce_only": True,
                }
            },
        )
    assert transport.calls[0] == ("POST", path, expected)
    assert result.filled_quantity == Decimal("0.2")


@pytest.mark.anyio
@pytest.mark.parametrize("side", list(OrderSide))
@pytest.mark.parametrize(
    ("kind", "purpose", "configuration", "stop_key"),
    [
        (OrderKind.STOP_LIMIT, IntentPurpose.STOP, "stop_limit_stop_limit_gtc", "stop_price"),
        (
            OrderKind.TRIGGER_BRACKET,
            IntentPurpose.BRACKET,
            "trigger_bracket_gtc",
            "stop_trigger_price",
        ),
    ],
)
async def test_standalone_protection_shapes(
    side: OrderSide, kind: OrderKind, purpose: IntentPurpose, configuration: str, stop_key: str
) -> None:
    """Standalone request shaping is dormant pending supervised venue acceptance."""
    client = CLIENT.replace(":entry:", f":{purpose.value}:")
    transport = FakeTransport(posts={ORDERS: [{"success": False}]}, gets={})
    await place(
        transport, side=side, client_order_id=client, kind=kind, stop_trigger_price=Decimal("2400")
    )
    config = transport.calls[0][2]["order_configuration"][configuration]
    assert config["base_size"] == "2"
    assert config["limit_price"] == "2500"
    assert config[stop_key] == "2400"
    assert "attached_order_configuration" not in transport.calls[0][2]
    if kind is OrderKind.STOP_LIMIT:
        assert config["stop_direction"] == (
            "STOP_DIRECTION_STOP_DOWN" if side is OrderSide.SELL else "STOP_DIRECTION_STOP_UP"
        )
