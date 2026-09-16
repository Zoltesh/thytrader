"""Paper and live evaluate extra-TF LTF-list indicators on last-completed candles."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tests.execution.test_htf_filter import (
    _candle,
    _Catalog,
    _five_minute_htf_strategy,
    _htf_hours,
    _ltf_window,
)
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import DeploymentMode, DeploymentStatus, RuntimePhase
from thytrader.execution.paper import PaperBroker
from thytrader.execution.service import create_deployment
from thytrader.execution.signals import evaluate_latest_entry
from thytrader.market_data.models import MarketProduct
from thytrader.research.signal_evaluator import SignalEvaluationError
from thytrader.research.trace import EntryConditionOutcome
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint


def _product() -> MarketProduct:
    """Return BTC-USD venue increments."""
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


def _five_minute_extra_tf_strategy() -> StrategyDefinition:
    """Published 5m strategy with a 1h SMA on the LTF list."""
    payload = _five_minute_htf_strategy().model_dump(mode="python")
    payload["htf_filter"] = None
    payload["indicators"] = [
        {
            "id": "htf_sma",
            "kind": "sma",
            "input": "close",
            "parameters": {"period": 2},
            "timeframe": "1h",
        },
        {
            "id": "atr",
            "kind": "atr",
            "input": ["high", "low", "close"],
            "parameters": {"period": 2},
        },
    ]
    payload["entry"] = {
        "side": "long",
        "when": {
            "all": [
                {
                    "left": {"indicator": "htf_sma"},
                    "operator": "greater_than",
                    "right": {"literal": "20"},
                }
            ]
        },
        "cooldown_bars": 0,
        "max_open_positions": 1,
    }
    return StrategyDefinition.model_validate(payload)


def test_evaluate_latest_entry_holds_last_completed_indicator_tf_and_ignores_partial() -> None:
    """5m paper/live uses the last completed 1h extra-TF bar; noon is ignored."""
    strategy = _five_minute_extra_tf_strategy()
    extra = {"1h": _htf_hours()}
    through_ten_fifty = _ltf_window()[:-1]
    through_ten_fifty_five = _ltf_window()
    assert (
        evaluate_latest_entry(strategy, through_ten_fifty, (), extra)
        is EntryConditionOutcome.NOT_MATCHED
    )
    assert (
        evaluate_latest_entry(strategy, through_ten_fifty_five, (), extra)
        is EntryConditionOutcome.MATCHED
    )


def test_evaluate_latest_entry_reuses_htf_candles_when_extra_tf_matches_filter() -> None:
    """An extra TF equal to htf_filter.timeframe may reuse the HTF window."""
    payload = _five_minute_htf_strategy().model_dump(mode="python")
    indicators = list(payload["indicators"])
    indicators.append(
        {
            "id": "hour_sma",
            "kind": "sma",
            "input": "close",
            "parameters": {"period": 2},
            "timeframe": "1h",
        }
    )
    payload["indicators"] = indicators
    when = payload["entry"]["when"]
    assert isinstance(when, dict)
    children = list(when["all"])
    children.append(
        {
            "left": {"indicator": "hour_sma"},
            "operator": "greater_than",
            "right": {"literal": "20"},
        }
    )
    when["all"] = children
    strategy = StrategyDefinition.model_validate(payload)
    through_ten_fifty_five = _ltf_window()
    assert (
        evaluate_latest_entry(strategy, through_ten_fifty_five, _htf_hours())
        is EntryConditionOutcome.MATCHED
    )


def test_evaluate_latest_entry_crossover_fires_when_indicator_timeframe_rolls() -> None:
    """Paper/live extra-TF crossovers fire when that clock rolls, not on inner LTF bars."""
    payload = _five_minute_extra_tf_strategy().model_dump(mode="python")
    indicators = list(payload["indicators"])
    indicators.insert(1, {"id": "level", "kind": "constant", "parameters": {"value": "20"}})
    payload["indicators"] = indicators
    payload["entry"] = {
        "side": "long",
        "when": {
            "all": [
                {
                    "left": {"indicator": "htf_sma"},
                    "operator": "crosses_above",
                    "right": {"indicator": "level"},
                }
            ]
        },
        "cooldown_bars": 0,
        "max_open_positions": 1,
    }
    strategy = StrategyDefinition.model_validate(payload)
    extra = {"1h": _htf_hours()}
    through_ten_fifty = _ltf_window()[:-1]
    through_ten_fifty_five = _ltf_window()
    through_eleven = (
        *_ltf_window(),
        _candle(datetime(2026, 7, 10, 11, tzinfo=UTC), "100"),
    )
    assert (
        evaluate_latest_entry(strategy, through_ten_fifty, (), extra)
        is EntryConditionOutcome.NOT_MATCHED
    )
    assert (
        evaluate_latest_entry(strategy, through_ten_fifty_five, (), extra)
        is EntryConditionOutcome.MATCHED
    )
    assert (
        evaluate_latest_entry(strategy, through_eleven, (), extra)
        is EntryConditionOutcome.NOT_MATCHED
    )


def test_evaluate_latest_entry_fails_closed_without_extra_tf_candles() -> None:
    """Missing extra-TF candles are a contract failure, not an implicit pass."""
    strategy = _five_minute_extra_tf_strategy()
    with pytest.raises(SignalEvaluationError, match="Candles are required for indicator timeframe"):
        evaluate_latest_entry(strategy, _ltf_window())


@pytest.mark.anyio
async def test_process_closed_bar_does_not_enter_when_extra_tf_fails() -> None:
    """Extra-TF-false is not an entry."""
    store = InMemoryExecutionStore()
    strategy = _five_minute_extra_tf_strategy()
    created = await create_deployment(
        store=store,
        publication_store=_Catalog(strategy),
        strategy_fingerprint=strategy_fingerprint(strategy),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal("10000"),
        live_allowed=False,
    )
    snapshot = await store.get_deployment(created.id)
    updated = await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=_ltf_window()[:-1],
        broker=PaperBroker(),
        store=store,
        indicator_timeframe_candles={"1h": _htf_hours()},
    )
    assert updated.deployment.phase is RuntimePhase.FLAT
    assert updated.deployment.last_signal == EntryConditionOutcome.NOT_MATCHED.value
    assert updated.position is None


@pytest.mark.anyio
async def test_process_closed_bar_enters_when_extra_tf_matches() -> None:
    """Last-completed extra-TF match rests a paper entry."""
    store = InMemoryExecutionStore()
    strategy = _five_minute_extra_tf_strategy()
    created = await create_deployment(
        store=store,
        publication_store=_Catalog(strategy),
        strategy_fingerprint=strategy_fingerprint(strategy),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal("10000"),
        live_allowed=False,
    )
    snapshot = await store.get_deployment(created.id)
    updated = await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=_ltf_window(),
        broker=PaperBroker(),
        store=store,
        indicator_timeframe_candles={"1h": _htf_hours()},
    )
    assert updated.deployment.phase is RuntimePhase.PENDING_ENTRY
    assert updated.deployment.last_signal == EntryConditionOutcome.MATCHED.value


@pytest.mark.anyio
async def test_process_closed_bar_pauses_when_required_extra_tf_bar_is_missing() -> None:
    """Extra-TF coverage holes pause instead of trading without the published overlay."""
    store = InMemoryExecutionStore()
    strategy = _five_minute_extra_tf_strategy()
    created = await create_deployment(
        store=store,
        publication_store=_Catalog(strategy),
        strategy_fingerprint=strategy_fingerprint(strategy),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal("10000"),
        live_allowed=False,
    )
    snapshot = await store.get_deployment(created.id)
    updated = await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=_ltf_window(),
        broker=PaperBroker(),
        store=store,
    )
    assert updated.deployment.status is DeploymentStatus.PAUSED
