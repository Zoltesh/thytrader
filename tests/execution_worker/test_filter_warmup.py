"""Required-clock loaders cover the first decision bar's previous mapped warmup."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo

import pytest

from tests.execution.decision_support import paper_book
from tests.execution.test_htf_filter import _five_minute_htf_strategy
from tests.worker_patching import patch_worker_global
from thytrader.evaluation.signal_evaluator import SignalEvaluationError
from thytrader.evaluation.trace import EntryConditionOutcome
from thytrader.execution.decision_journal import decision_journal_scope
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import DecisionOutcome
from thytrader.execution.paper import PaperBroker
from thytrader.execution.signals import evaluate_latest_entry
from thytrader.execution_worker import service
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import Candle, CandleInterval, CandleRangeReport
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.service import MarketDataService
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.strategies.models import StrategyDefinition
from thytrader.trading.models import DeploymentStatus

pytestmark = pytest.mark.anyio
_ANCHOR = datetime(2026, 10, 6, 0, 44, 25, tzinfo=UTC)


class _Clock(datetime):
    """Fix the worker's observation time independently of the deployment anchor."""

    instant = _ANCHOR

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        """Return the fixed aware worker instant."""
        del tz
        return cls.instant


class _MissingEdge(DemoMarketData):
    """Omit a required edge bar without turning it into an interior no-trade bar."""

    def __init__(self, *, oldest: bool) -> None:
        """Choose the missing oldest warmup or newest completed candle."""
        self.oldest = oldest

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Preserve all real candles except the chosen edge of the required clock."""
        report = await super().get_historical_range(product_id, interval, starts_at, ends_at, now)
        candles = report.quality.candles
        if interval is CandleInterval.FOUR_HOURS:
            candles = candles[1:] if self.oldest else candles[:-1]
        return analyze_range(candles, interval, starts_at, ends_at, now)


def _strategy(*, extra_clock: bool = False) -> StrategyDefinition:
    """Declare the affected 1h/4h clocks with a fifty-candle EMA warmup."""
    payload = _five_minute_htf_strategy().model_dump(mode="python")
    payload["timeframe"] = "1h"
    payload["data_requirements"]["warmup_bars"] = 50
    htf = payload["htf_filter"]
    htf["timeframe"] = "4h"
    htf["data_requirements"]["warmup_bars"] = 50
    htf["indicators"] = [
        {"id": "htf_sma", "kind": "ema", "input": "close", "parameters": {"period": 50}}
    ]
    if extra_clock:
        payload["htf_filter"] = None
        payload["indicators"] = [
            *payload["indicators"],
            {**htf["indicators"][0], "timeframe": "4h"},
        ]
        payload["entry"]["when"] = htf["when"]
    return StrategyDefinition.model_validate(payload)


async def _decision_window(
    market_data: MarketDataService, definition: StrategyDefinition
) -> tuple[Candle, ...]:
    """Load the real worker decision clock so the evaluator sees the same first bar."""
    _product, candles, _expected = await service._closed_window(
        market_data, definition, deploy_anchor=_ANCHOR
    )
    return candles


@pytest.mark.parametrize("hour", [0, 1, 4])
async def test_htf_loader_covers_first_bar_and_later_clock_rollovers(
    monkeypatch: pytest.MonkeyPatch, hour: int
) -> None:
    """A complete provider window evaluates at startup, within a bucket, and after restart."""
    monkeypatch.setattr(_Clock, "instant", _ANCHOR + timedelta(hours=hour))
    patch_worker_global(monkeypatch, "datetime", _Clock)
    definition = _strategy()
    market_data = MarketDataService(DemoMarketData())
    decision = await _decision_window(market_data, definition)
    htf = await service._closed_htf_window(market_data, definition, deploy_anchor=_ANCHOR)

    assert htf is not None
    assert evaluate_latest_entry(definition, decision, htf) is EntryConditionOutcome.MATCHED
    assert htf[0].starts_at == datetime(2026, 9, 27, 12, tzinfo=UTC)
    assert all(candle.starts_at + timedelta(hours=4) <= _Clock.instant for candle in htf)


async def test_extra_indicator_loader_covers_previous_mapped_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unbound 4h indicator needs the same preceding warmup as an HTF filter."""
    monkeypatch.setattr(_Clock, "instant", _ANCHOR)
    patch_worker_global(monkeypatch, "datetime", _Clock)
    definition = _strategy(extra_clock=True)
    market_data = MarketDataService(DemoMarketData())
    decision = await _decision_window(market_data, definition)
    extra = await service._closed_indicator_timeframe_windows(
        market_data, definition, (), deploy_anchor=_ANCHOR
    )

    assert extra is not None
    assert evaluate_latest_entry(definition, decision, (), extra) is EntryConditionOutcome.MATCHED


async def test_deploy_within_htf_bucket_keeps_existing_warmup_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first decision wholly inside an HTF bucket needs no extra warmup padding."""
    anchor = _ANCHOR + timedelta(hours=1)
    monkeypatch.setattr(_Clock, "instant", anchor)
    patch_worker_global(monkeypatch, "datetime", _Clock)
    definition = _strategy()
    market_data = MarketDataService(DemoMarketData())
    _product, decision, _expected = await service._closed_window(
        market_data, definition, deploy_anchor=anchor
    )
    htf = await service._closed_htf_window(market_data, definition, deploy_anchor=anchor)

    assert htf is not None
    assert len(htf) == 50
    assert htf[0].starts_at == datetime(2026, 9, 27, 16, tzinfo=UTC)
    assert evaluate_latest_entry(definition, decision, htf) is EntryConditionOutcome.MATCHED


async def test_worker_evaluates_first_decision_without_false_coverage_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real worker journals a no-signal decision and keeps a complete-data bot running."""
    monkeypatch.setattr(_Clock, "instant", _ANCHOR)
    patch_worker_global(monkeypatch, "datetime", _Clock)
    patch_worker_global(monkeypatch, "utc_now", _Clock.now)
    payload = _strategy().model_dump(mode="python")
    payload["entry"]["when"] = {
        "all": [{"left": {"literal": "1"}, "operator": "greater_than", "right": {"literal": "2"}}]
    }
    definition = StrategyDefinition.model_validate(payload)
    store, snapshot = await paper_book(definition)
    await store.save_deployment(replace(snapshot.deployment, created_at=_ANCHOR))
    snapshot = await store.get_deployment(snapshot.deployment.id)
    journal = InMemoryDecisionJournalStore()

    with decision_journal_scope(journal):
        await service._advance_strategy(
            snapshot,
            strategy=definition,
            store=store,
            market_data=MarketDataService(DemoMarketData()),
            paper_broker=PaperBroker(),
            live_broker=None,
            quote_reader=None,
            risk_policy=compiled_default_risk_policy(),
            portfolio=(snapshot,),
            user_feed_store=None,
            memory_store=None,
        )

    after = await store.get_deployment(snapshot.deployment.id)
    assert after.deployment.status is DeploymentStatus.RUNNING
    assert after.deployment.mismatch_detail is None
    assert after.deployment.last_evaluated_bar == datetime(2026, 10, 5, 23, tzinfo=UTC)
    assert after.intents == after.orders == after.fills == ()
    (row,) = journal.rows()
    assert row.bar_starts_at == after.deployment.last_evaluated_bar
    assert row.outcome is DecisionOutcome.NO_SIGNAL
    assert row.rule is not None


@pytest.mark.parametrize("oldest", [True, False])
async def test_real_missing_required_edges_still_fail_closed(
    monkeypatch: pytest.MonkeyPatch, oldest: bool
) -> None:
    """Loading more warmup must neither fabricate an edge nor waive evaluator validation."""
    monkeypatch.setattr(_Clock, "instant", _ANCHOR)
    patch_worker_global(monkeypatch, "datetime", _Clock)
    definition = _strategy()
    market_data = MarketDataService(_MissingEdge(oldest=oldest))
    decision = await _decision_window(market_data, definition)
    htf = await service._closed_htf_window(market_data, definition, deploy_anchor=_ANCHOR)

    if oldest:
        assert htf is not None
        with pytest.raises(SignalEvaluationError, match="HTF candle coverage"):
            evaluate_latest_entry(definition, decision, htf)
    else:
        assert htf is None
