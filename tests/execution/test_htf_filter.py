"""Paper and live evaluate published HTF filters on last-completed candles."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from thytrader.execution.loop import process_closed_bar
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import DeploymentMode, DeploymentStatus, RuntimePhase
from thytrader.execution.paper import PaperBroker
from thytrader.execution.service import create_deployment
from thytrader.execution.signals import evaluate_latest_entry
from thytrader.market_data.models import Candle, MarketProduct
from thytrader.research.multi_timeframe import htf_bars_closed_at_or_before, mapped_htf_start
from thytrader.research.signal_evaluator import SignalEvaluationError
from thytrader.research.trace import EntryConditionOutcome
from thytrader.strategies.authoring import create_reference_draft
from thytrader.strategies.models import StrategyDefinition, StrategyStatus, strategy_fingerprint
from thytrader.strategies.publication import PublishedStrategy, StrategyPublicationError


class _Catalog:
    """Load-only StrategyPublicationStore double for one published strategy."""

    def __init__(self, definition: StrategyDefinition) -> None:
        """Bind one immutable publication."""
        fingerprint = strategy_fingerprint(definition)
        self._published = PublishedStrategy(strategy_fingerprint=fingerprint, definition=definition)

    async def publish(self, definition: StrategyDefinition) -> PublishedStrategy:
        """Refuse extra publications; this fixture only serves load()."""
        del definition
        raise StrategyPublicationError("Catalog fixture is load-only.")

    async def publish_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> PublishedStrategy:
        """Refuse draft publication; this fixture only serves load()."""
        del definition, expected_revision
        raise StrategyPublicationError("Catalog fixture is load-only.")

    async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
        """Return the bound publication or fail closed."""
        if strategy_fingerprint_value != self._published.strategy_fingerprint:
            raise StrategyPublicationError("Published strategy was not found.")
        return self._published


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


def _candle(starts_at: datetime, close: str) -> Candle:
    """Build one geometrically valid OHLCV bar."""
    value = Decimal(close)
    return Candle(
        starts_at=starts_at,
        open=value,
        high=value + Decimal("1"),
        low=value - Decimal("0.5") if value > 1 else value,
        close=value,
        volume=Decimal("10"),
    )


def _five_minute_htf_strategy() -> StrategyDefinition:
    """Published 5m strategy with always-true LTF entry and a 1h SMA HTF filter."""
    draft = create_reference_draft(now=datetime(2026, 1, 1, tzinfo=UTC))
    payload = draft.model_dump(mode="python")
    payload["status"] = StrategyStatus.PUBLISHED.value
    payload["timeframe"] = "5m"
    payload["data_requirements"] = {
        "warmup_bars": 2,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    payload["indicators"] = [
        {"id": "sma", "kind": "sma", "input": "close", "parameters": {"period": 2}},
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
                    "left": {"literal": "1"},
                    "operator": "greater_than_or_equal",
                    "right": {"literal": "0"},
                }
            ]
        },
        "cooldown_bars": 0,
        "max_open_positions": 1,
    }
    payload["exits"] = {
        "initial_stop": {"kind": "atr_multiple", "atr_indicator": "atr", "multiple": "2"},
        "take_profit": {"kind": "reward_risk", "multiple": "2"},
        "trailing_stop": {"enabled": False},
        "time_exit": {"max_bars_held": 96},
    }
    payload["htf_filter"] = {
        "timeframe": "1h",
        "data_requirements": {
            "warmup_bars": 2,
            "required_fields": ["open", "high", "low", "close", "volume"],
        },
        "indicators": [
            {"id": "htf_sma", "kind": "sma", "input": "close", "parameters": {"period": 2}},
        ],
        "when": {
            "all": [
                {
                    "left": {"indicator": "htf_sma"},
                    "operator": "greater_than",
                    "right": {"literal": "20"},
                }
            ]
        },
    }
    return StrategyDefinition.model_validate(payload)


def _one_minute_five_minute_htf_strategy() -> StrategyDefinition:
    """Published 1m strategy with a 5m HTF filter (venue clocks from ADR 0040)."""
    payload = _five_minute_htf_strategy().model_dump(mode="python")
    payload["timeframe"] = "1m"
    htf = payload["htf_filter"]
    assert isinstance(htf, dict)
    htf["timeframe"] = "5m"
    return StrategyDefinition.model_validate(payload)


def _ltf_window() -> tuple[Candle, ...]:
    """Return warmup and evaluation 5m bars from 10:40 through 10:55."""
    start = datetime(2026, 7, 10, 10, 40, tzinfo=UTC)
    return tuple(_candle(start + timedelta(minutes=5 * index), "100") for index in range(4))


def _htf_hours(*, include_partial_noon: bool = True) -> tuple[Candle, ...]:
    """Return closed 1h bars; optional 12:00 bar is a lookahead poison pill."""
    day = datetime(2026, 7, 10, tzinfo=UTC)
    bars = (
        _candle(day.replace(hour=8), "1"),
        _candle(day.replace(hour=9), "1"),
        _candle(day.replace(hour=10), "50"),
        _candle(day.replace(hour=11), "10"),
    )
    if include_partial_noon:
        bars = (*bars, _candle(day.replace(hour=12), "1"))
    return bars


def test_one_minute_ltf_may_use_five_minute_htf() -> None:
    """Venue-clock HTF pairs stay integer-multiple; 1m may filter on 5m."""
    assert _one_minute_five_minute_htf_strategy().htf_filter is not None
    assert mapped_htf_start(datetime(2026, 7, 10, 10, 5, tzinfo=UTC), "5m") == datetime(
        2026, 7, 10, 10, 0, tzinfo=UTC
    )


def test_evaluate_latest_entry_one_minute_holds_completed_five_minute_htf() -> None:
    """1m paper/live uses last-completed 5m HTF and drops an in-progress 5m bar."""
    strategy = _one_minute_five_minute_htf_strategy()
    ltf_start = datetime(2026, 7, 10, 10, 0, tzinfo=UTC)
    ltf = tuple(_candle(ltf_start + timedelta(minutes=index), "100") for index in range(6))
    five = datetime(2026, 7, 10, 9, 50, tzinfo=UTC)
    htf = (
        _candle(five, "1"),
        _candle(five + timedelta(minutes=5), "1"),
        _candle(five + timedelta(minutes=10), "50"),
        _candle(five + timedelta(minutes=15), "1"),
    )
    through_10_03 = ltf[:4]
    through_10_04 = ltf[:5]
    assert evaluate_latest_entry(strategy, through_10_03, htf) is EntryConditionOutcome.NOT_MATCHED
    assert evaluate_latest_entry(strategy, through_10_04, htf) is EntryConditionOutcome.MATCHED


def test_evaluate_latest_entry_holds_last_completed_htf_and_ignores_partial() -> None:
    """5m paper/live uses the last completed 1h bar; an in-progress noon hour is ignored."""
    strategy = _five_minute_htf_strategy()
    through_ten_fifty = _ltf_window()[:-1]
    through_ten_fifty_five = _ltf_window()
    htf = _htf_hours()
    assert (
        evaluate_latest_entry(strategy, through_ten_fifty, htf) is EntryConditionOutcome.NOT_MATCHED
    )
    assert (
        evaluate_latest_entry(strategy, through_ten_fifty_five, htf)
        is EntryConditionOutcome.MATCHED
    )


def test_evaluate_latest_entry_fails_closed_when_completed_htf_bar_is_missing() -> None:
    """Missing last-completed HTF coverage cannot become an implicit pass."""
    strategy = _five_minute_htf_strategy()
    incomplete = _htf_hours(include_partial_noon=False)[:2]
    with pytest.raises(SignalEvaluationError, match="HTF candle coverage"):
        evaluate_latest_entry(strategy, _ltf_window(), incomplete)


def test_evaluate_latest_entry_rejects_htf_candles_without_filter() -> None:
    """A single-timeframe strategy must not silently consume extra HTF candles."""
    draft = create_reference_draft(now=datetime(2026, 1, 1, tzinfo=UTC))
    payload = draft.model_dump(mode="python")
    payload["status"] = StrategyStatus.PUBLISHED.value
    strategy = StrategyDefinition.model_validate(payload)
    with pytest.raises(SignalEvaluationError, match="without an HTF filter"):
        evaluate_latest_entry(strategy, _ltf_window(), _htf_hours())


def test_evaluate_latest_entry_requires_htf_candles_when_filter_present() -> None:
    """Paper/live must not ignore a published HTF filter."""
    with pytest.raises(SignalEvaluationError, match="HTF candles are required"):
        evaluate_latest_entry(_five_minute_htf_strategy(), _ltf_window())


def test_htf_bars_closed_at_or_before_drops_in_progress_hour() -> None:
    """Exclusive HTF close after the LTF close is lookahead and must be dropped."""
    visible = htf_bars_closed_at_or_before(
        _htf_hours(),
        close_at=datetime(2026, 7, 10, 11, tzinfo=UTC),
        htf_timeframe="1h",
    )
    assert [candle.starts_at.hour for candle in visible] == [8, 9, 10]


@pytest.mark.anyio
async def test_create_deployment_starts_htf_filter_paper_and_live() -> None:
    """Published HTF-filter strategies may start paper and live."""
    strategy = _five_minute_htf_strategy()
    catalog = _Catalog(strategy)
    paper = await create_deployment(
        store=InMemoryExecutionStore(),
        publication_store=catalog,
        strategy_fingerprint=strategy_fingerprint(strategy),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal("10000"),
        live_allowed=False,
    )
    assert paper.status is DeploymentStatus.RUNNING
    live = await create_deployment(
        store=InMemoryExecutionStore(),
        publication_store=catalog,
        strategy_fingerprint=strategy_fingerprint(strategy),
        mode=DeploymentMode.LIVE,
        paper_starting_cash=None,
        live_allowed=True,
    )
    assert live.mode is DeploymentMode.LIVE
    assert live.status is DeploymentStatus.RUNNING


@pytest.mark.anyio
async def test_process_closed_bar_does_not_enter_when_htf_filter_fails() -> None:
    """LTF-true / HTF-false is not an entry."""
    store = InMemoryExecutionStore()
    strategy = _five_minute_htf_strategy()
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
        htf_candles=_htf_hours(),
    )
    assert updated.deployment.phase is RuntimePhase.FLAT
    assert updated.deployment.last_signal == EntryConditionOutcome.NOT_MATCHED.value
    assert updated.position is None


@pytest.mark.anyio
async def test_process_closed_bar_enters_when_htf_and_ltf_match() -> None:
    """Combined HTF AND LTF match rests a paper entry."""
    store = InMemoryExecutionStore()
    strategy = _five_minute_htf_strategy()
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
        htf_candles=_htf_hours(),
    )
    assert updated.deployment.phase is RuntimePhase.PENDING_ENTRY
    assert updated.deployment.last_signal == EntryConditionOutcome.MATCHED.value


@pytest.mark.anyio
async def test_process_closed_bar_pauses_when_required_htf_bar_is_missing() -> None:
    """HTF coverage holes pause instead of trading without the published filter."""
    store = InMemoryExecutionStore()
    strategy = _five_minute_htf_strategy()
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
        htf_candles=_htf_hours(include_partial_noon=False)[:2],
    )
    assert updated.deployment.status is DeploymentStatus.PAUSED
    assert updated.deployment.mismatch_detail is not None
    assert "HTF" in updated.deployment.mismatch_detail
    assert updated.position is None
