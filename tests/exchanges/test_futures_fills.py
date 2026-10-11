"""Fail-closed futures fill ingestion with exact units and all-in commission."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, localcontext
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pytest

from tests.exchanges.test_coinbase_broker import FakeTransport
from tests.exchanges.test_coinbase_futures_broker import PRODUCT, Sizes
from thytrader.exchanges.coinbase_futures_broker import CoinbaseFuturesBroker
from thytrader.execution.broker import BrokerError

FILLS = "/api/v3/brokerage/orders/historical/fills"


def fixture_rows() -> list[dict[str, Any]]:
    """Load synthetic boundary JSON (Any is confined to venue payload tests)."""
    path = Path(__file__).parent / "fixtures/coinbase_futures/orders_historical_fills.json"
    return json.loads(path.read_text())["body"]["fills"]


@pytest.mark.anyio
async def test_futures_fills_paginate_convert_and_keep_all_in_fee() -> None:
    """Commission detail's rounded total must never replace actual commission."""
    rows = fixture_rows()
    transport = FakeTransport(
        gets={FILLS: [{"fills": rows[:1], "cursor": "next"}, {"fills": rows[1:], "cursor": ""}]}
    )
    broker = CoinbaseFuturesBroker(transport, Sizes())
    assert hasattr(broker, "list_fills"), "futures fills not implemented"
    fills = await broker.list_fills(product_id=PRODUCT)
    assert [fill.quantity for fill in fills] == [Decimal("0.1"), Decimal("0.1")]
    assert [fill.fee for fill in fills] == [Decimal("0.359"), Decimal("0.36")]
    namespace = uuid5(NAMESPACE_URL, "https://thytrader.dev/coinbase/fill")
    assert fills[0].id == uuid5(namespace, rows[0]["trade_id"])
    assert transport.calls[0][2] == {
        "product_ids": [PRODUCT],
        "product_types": ["FUTURE"],
        "limit": 100,
    }
    assert transport.calls[1][2]["cursor"] == "next"


@pytest.mark.anyio
async def test_order_fills_send_only_order_filter() -> None:
    """Coinbase disallows order_ids combined with product filters; validate locally."""
    row = fixture_rows()[0]
    transport = FakeTransport(gets={FILLS: [{"fills": [row]}]})
    broker = CoinbaseFuturesBroker(transport, Sizes())
    assert hasattr(broker, "list_fills")
    assert len(await broker.list_fills(product_id=PRODUCT, order_id=row["order_id"])) == 1
    assert transport.calls[0][2] == {"order_ids": [row["order_id"]], "limit": 100}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [
        {"product_type": "SPOT"},
        {"product_type": None},
        {"product_id": "BTC-USD"},
        {"trade_type": "ADJUSTMENT"},
        {"trade_type": None},
        {"future_legs": [{}]},
        {"future_legs": "bad"},
        {"future_legs": None},
        {"option_legs": [{}]},
        {"size_in_quote": True},
        {"size_in_quote": "false"},
        {"size_in_quote": None},
        {"size": "1.5"},
        {"size": "0"},
        {"size": "NaN"},
        {"size": True},
        {"size": 1.0},
        {"commission": None},
        {"commission": "-1"},
        {"commission": 0.359},
        {"commission": "sNaN"},
        {"price": "0"},
        {"price": "Infinity"},
        {"trade_time": "not-time"},
        {"trade_time": "2026-01-01T00:00:00"},
        {"order_id": "wrong"},
        {"trade_id": None, "entry_id": None},
    ],
)
async def test_unsafe_fill_quarantines_whole_page(changes: dict[str, Any]) -> None:
    """Never skip malformed rows or guess unknown financial evidence."""
    row = deepcopy(fixture_rows()[0])
    order_id = row["order_id"]
    row.update(changes)
    transport = FakeTransport(gets={FILLS: [{"fills": [row]}]})
    broker = CoinbaseFuturesBroker(transport, Sizes())
    assert hasattr(broker, "list_fills")
    with pytest.raises(BrokerError):
        await broker.list_fills(product_id=PRODUCT, order_id=order_id)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payloads",
    [
        [{}],
        [{"fills": None}],
        [{"fills": {}}],
        [{"fills": [None]}],
        [{"fills": [], "proof_token_required": True}],
        [{"fills": [], "has_next": True}],
        [{"fills": [], "cursor": 123}],
        [{"fills": [], "cursor": "same"}, {"fills": [], "cursor": "same"}],
        [{"fills": [], "cursor": str(i)} for i in range(20)],
    ],
)
async def test_incomplete_pagination_never_claims_complete(payloads: list[dict[str, Any]]) -> None:
    """Malformed/missing rows, cursors and incomplete streams cannot become empty history."""
    broker = CoinbaseFuturesBroker(FakeTransport(gets={FILLS: payloads}), Sizes())
    assert hasattr(broker, "list_fills")
    with pytest.raises(BrokerError):
        await broker.list_fills(product_id=PRODUCT)


@pytest.mark.anyio
async def test_unknown_fill_size_denies_before_transport() -> None:
    """A missing catalog is never interpreted as size one."""
    transport = FakeTransport(gets={})
    broker = CoinbaseFuturesBroker(transport, Sizes(None))
    assert hasattr(broker, "list_fills")
    with pytest.raises(BrokerError):
        await broker.list_fills(product_id=PRODUCT)
    assert transport.calls == []


@pytest.mark.anyio
async def test_inexact_base_conversion_is_not_ledger_evidence() -> None:
    """Even all traps disabled cannot silently lose base units at the boundary."""
    rows = fixture_rows()[:1]
    rows[0]["size"] = "3"
    broker = CoinbaseFuturesBroker(
        FakeTransport(gets={FILLS: [{"fills": rows}]}), Sizes(Decimal("0.123"))
    )
    assert hasattr(broker, "list_fills")
    with localcontext() as context:
        context.prec = 2
        context.clear_traps()
        with pytest.raises(BrokerError):
            await broker.list_fills(product_id=PRODUCT)
