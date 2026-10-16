"""A futures strategy can gate entries on a spot reference instrument (ADR 0145).

The perp settles in USD, so its BTC reference is BTC-USD (the same candles as BTC-USDC on
Coinbase). The reference only feeds indicator values; it never enters capital or margin.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
import pytest

from tests.backtest.test_kernel_futures import _HOLD, _spec, _strategy
from tests.backtest.test_reference_kernel import _FIRST_DAY
from tests.strategies.reference_support import REFERENCE_DATASET, daily_bars
from thytrader.backtest.kernel import simulate_backtest
from thytrader.evaluation.models import ReferenceInstrumentDataset, ResearchRunSpecification
from thytrader.strategies.models import StrategyDefinition


def _gated(reference_product: str = "BTC-USD") -> StrategyDefinition:
    """The futures fixture AND the reference's last closed daily close above its 2-day SMA."""
    payload: dict[str, Any] = _strategy().model_dump(mode="python")
    payload["data_requirements"]["reference_instruments"] = (
        {"id": "btc", "product_id": reference_product, "timeframe": "1d"},
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
    rule = payload["entry"]["when"]["all"][0]
    payload["entry"]["when"] = {
        "all": (
            rule,
            {
                "left": {"indicator": "btc_close"},
                "operator": "greater_than",
                "right": {"indicator": "btc_sma"},
            },
        )
    }
    return StrategyDefinition.model_validate(payload)


def _run(strategy: StrategyDefinition) -> ResearchRunSpecification:
    """The futures run with the BTC reference bound."""
    run = _spec(strategy)
    return ResearchRunSpecification.model_validate(
        {
            **run.model_dump(mode="python"),
            "reference_dataset_fingerprints": (
                ReferenceInstrumentDataset(
                    reference_id="btc",
                    product_id="BTC-USD",
                    timeframe="1d",
                    dataset_fingerprint=REFERENCE_DATASET,
                ),
            ),
        }
    )


def test_a_bullish_reference_lets_the_perp_trade_whole_contracts() -> None:
    """BTC's last close 200 is above its SMA 150, so the perp enters as without the gate."""
    strategy = _gated()
    result = simulate_backtest(
        _run(strategy),
        strategy,
        _HOLD,
        reference_candles={"btc": daily_bars(_FIRST_DAY, "100", "200", "1")},
    )
    (trade,) = result.trades
    assert trade.entry.quantity == "16.6"
    assert trade.funding == "-0.0249"


def test_a_bearish_reference_blocks_every_perp_entry() -> None:
    """BTC's last close 50 is below its SMA 75: the same perp path never enters."""
    strategy = _gated()
    result = simulate_backtest(
        _run(strategy),
        strategy,
        _HOLD,
        reference_candles={"btc": daily_bars(_FIRST_DAY, "100", "50", "1")},
    )
    assert result.trades == ()


def test_a_futures_reference_must_be_quoted_in_usd() -> None:
    """A USDC reference does not match the USD-settled perp's quote currency."""
    with pytest.raises(ValidationError, match="strategy quote currency USD"):
        _gated("BTC-USDC")
