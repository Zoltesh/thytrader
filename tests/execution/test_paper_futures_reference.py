"""A paper futures book evaluates a spot reference gate like a spot book (ADR 0145).

The worker hands each closed bar's reference windows to ``process_closed_bar`` for every
book; a futures book reads them through the same per-product evaluation, so a bearish BTC
reference keeps the perp flat while a bullish one lets it rest its entry.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from tests.execution.test_paper_futures_books import (
    _START,
    _book,
    _candles,
    _policy,
    _product,
    _state,
    _strategy,
)
from tests.strategies.reference_support import daily_bars
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.paper import PaperBroker
from thytrader.strategies.models import StrategyDefinition
from thytrader.trading.futures_book import futures_book_scope
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import RuntimePhase


def _gated() -> StrategyDefinition:
    """The always-entering perp fixture AND BTC-USD's last daily close above its 2-day SMA."""
    payload: dict[str, Any] = _strategy().model_dump(mode="python")
    payload["data_requirements"]["reference_instruments"] = (
        {"id": "btc", "product_id": "BTC-USD", "timeframe": "1d"},
    )
    payload["indicators"] = (
        *payload["indicators"],
        {
            "id": "btc_close",
            "kind": "identity",
            "input": "close",
            "parameters": {},
            "source": "btc",
        },
        {
            "id": "btc_sma",
            "kind": "sma",
            "input": "close",
            "parameters": {"period": 2},
            "source": "btc",
        },
    )
    payload["entry"]["when"] = {
        "all": (
            *payload["entry"]["when"]["all"],
            {
                "left": {"indicator": "btc_close"},
                "operator": "greater_than",
                "right": {"indicator": "btc_sma"},
            },
        )
    }
    return StrategyDefinition.model_validate(payload)


async def _after_one_bar(last_btc_close: str) -> RuntimePhase:
    """Process the 30th hourly bar (10-11 05:00) with BTC's 10-10 daily close as given."""
    store = InMemoryExecutionStore()
    strategy = _gated()
    snapshot = await _book(store, strategy)
    btc = daily_bars(_START - timedelta(days=2), "100", "100", last_btc_close, "1")
    with futures_book_scope(_state(snapshot)):
        after = await process_closed_bar(
            snapshot,
            strategy=strategy,
            product=_product(),
            candles=_candles(30),
            broker=PaperBroker(),
            store=store,
            risk_policy=_policy(),
            reference_candles={"btc": btc},
        )
    return after.deployment.phase


@pytest.mark.anyio
async def test_a_bullish_reference_lets_the_paper_perp_rest_its_entry() -> None:
    """BTC 200 above its SMA 150: the perp rests an entry, as without the gate."""
    assert await _after_one_bar("200") is RuntimePhase.PENDING_ENTRY


@pytest.mark.anyio
async def test_a_bearish_reference_keeps_the_paper_perp_flat() -> None:
    """BTC 50 below its SMA 75: the same bar never enters."""
    assert await _after_one_bar("50") is RuntimePhase.FLAT
