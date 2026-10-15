"""Worker windows match the start-time history plan and hold their warmup (ADR 0140)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.execution.decision_support import Catalog
from tests.execution.test_htf_filter import _startable_htf_strategy
from tests.execution.test_reference_runtime import _eth, _watchlist
from tests.execution_worker.test_window_longevity import _pin, _union_strategy
from tests.market_history import listed_history
from tests.strategies.reference_support import (
    candle,
    daily_bars,
    hourly_bars,
    reference_strategy,
)
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.evaluation.multi_timeframe import closed_bar_required_coverage, ltf_close
from thytrader.execution import stopped as stopped_module
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.clock_windows import (
    ClockWindow,
    deployment_clock_windows,
    extra_timeframe_warmup,
    windows_hold_warmup,
)
from thytrader.execution.decision_journal import decision_journal_scope
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import DecisionSkipReason
from thytrader.execution.paper import PaperBroker
from thytrader.execution.service import ReferenceWatchlist, create_deployment
from thytrader.execution_worker import service, stopped_step, windows as windows_module
from thytrader.execution_worker.windows import SHORT_HISTORY_DETAIL
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.warmup_policy import with_convergence_warmup
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, DeploymentSnapshot, DeploymentStatus

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from thytrader.market_data.models import Candle, MarketProduct

pytestmark = pytest.mark.anyio

_ANCHORS = (
    datetime(2026, 10, 6, 0, 44, 25, tzinfo=UTC),
    datetime(2026, 10, 6, 3, 59, 59, tzinfo=UTC),
    datetime(2026, 10, 6, 4, 0, 0, tzinfo=UTC),
    datetime(2026, 10, 6, 6, 0, 1, tzinfo=UTC),
)


def _three_clock_strategy() -> StrategyDefinition:
    """1h decisions, a 4h EMA(50) filter with a 4h EMA(80), and a 2h EMA(30), converged."""
    payload = _union_strategy().model_dump(mode="python")
    payload["indicators"] = [
        *payload["indicators"],
        {
            "id": "mid_ema",
            "kind": "ema",
            "input": "close",
            "parameters": {"period": 30},
            "timeframe": "2h",
        },
    ]
    return with_convergence_warmup(StrategyDefinition.model_validate(payload))


@pytest.mark.parametrize("anchor", _ANCHORS)
async def test_worker_windows_start_where_the_start_check_looked(
    monkeypatch: pytest.MonkeyPatch, anchor: datetime
) -> None:
    """Each fetched window starts at its planned bar and holds its warmup at the first bar."""
    _pin(monkeypatch, anchor)
    strategy = _three_clock_strategy()
    market_data = MarketDataService(DemoMarketData())
    _product, decision, _last = await service._closed_window(
        market_data, strategy, deploy_anchor=anchor
    )
    htf = await service._closed_htf_window(market_data, strategy, deploy_anchor=anchor)
    assert htf is not None
    extra = await service._closed_indicator_timeframe_windows(
        market_data, strategy, htf, deploy_anchor=anchor
    )
    assert extra is not None
    planned = {
        (window.product_id, window.timeframe): window.starts_at
        for window in deployment_clock_windows(strategy, deploy_anchor=anchor)
    }
    assert planned == {
        ("BTC-USD", "1h"): decision[0].starts_at,
        ("BTC-USD", "4h"): htf[0].starts_at,
        ("BTC-USD", "2h"): extra["2h"][0].starts_at,
    }
    assert windows_hold_warmup(strategy, decision, htf, extra)
    # Windows starting exactly at the evaluator's required start hold; one bar later does not.
    latest = decision[-1].starts_at
    close = ltf_close(latest, strategy.timeframe)
    decision_warmup = strategy.data_requirements.warmup_bars
    assert windows_hold_warmup(strategy, decision[-decision_warmup:], htf, extra)
    assert not windows_hold_warmup(strategy, decision[-(decision_warmup - 1) :], htf, extra)
    htf_filter = strategy.htf_filter
    assert htf_filter is not None
    clocks = {
        "4h": max(
            htf_filter.data_requirements.warmup_bars, extra_timeframe_warmup(strategy, "4h") or 0
        ),
        "2h": extra_timeframe_warmup(strategy, "2h") or 0,
    }
    for timeframe, warmup in clocks.items():
        required_start, _end = closed_bar_required_coverage(
            evaluation_starts_at=latest,
            evaluation_ends_at=close,
            timeframe=timeframe,
            warmup_bars=warmup,
        )
        window = extra[timeframe]
        exact = tuple(item for item in window if item.starts_at >= required_start)
        late = exact[1:]
        assert exact[0].starts_at == required_start
        for bars, holds in ((exact, True), (late, False)):
            shifted = {**extra, timeframe: bars}
            htf_bars = bars if timeframe == "4h" else htf
            assert windows_hold_warmup(strategy, decision, htf_bars, shifted) is holds


@pytest.mark.parametrize("anchor", _ANCHORS)
async def test_reference_windows_start_where_the_start_check_looked(
    monkeypatch: pytest.MonkeyPatch, anchor: datetime
) -> None:
    """The reference window the worker fetches is the one the start checked."""
    _pin(monkeypatch, anchor)
    strategy = with_convergence_warmup(reference_strategy())
    references = await service._closed_reference_windows(
        MarketDataService(DemoMarketData()), strategy, deploy_anchor=anchor
    )
    (_decision, reference) = deployment_clock_windows(strategy, deploy_anchor=anchor)
    assert references["btc"][0].starts_at == reference.starts_at


async def test_a_young_market_journals_warmup_and_keeps_running() -> None:
    """A book whose market is younger than its warmup waits without entering or pausing."""
    strategy = create_template_strategy(product_id="BTC-USD", timeframe="1h")
    assert strategy.data_requirements.warmup_policy == "convergence"
    store = InMemoryExecutionStore()
    # Started without the history check, as a book predating it would have been.
    await create_deployment(
        store=store,
        publication_store=Catalog(strategy),
        strategy_fingerprint=strategy_fingerprint(strategy),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal("10000"),
        live_allowed=False,
    )
    listed = datetime.now(UTC) - timedelta(hours=strategy.data_requirements.warmup_bars // 2)
    journal = InMemoryDecisionJournalStore()
    with execution_audit_scope(InMemoryAuditEventStore()), decision_journal_scope(journal):
        await service._run_cycle(
            store=store,
            publication_store=Catalog(strategy),
            market_data=listed_history(listed_at=listed),
            paper_broker=PaperBroker(),
            live_broker=None,
            quote_reader=None,
            risk_store=None,
        )
    (row,) = journal.rows()
    assert row.skip_reason is DecisionSkipReason.WARMUP
    assert row.summary == f"Skipped: warming up — {SHORT_HISTORY_DETAIL}"
    (deployment,) = await store.list_deployments()
    after = await store.get_deployment(deployment.id)
    assert after.deployment.status is DeploymentStatus.RUNNING
    assert after.intents == ()
    # The row records the decision window and that the venue's history began after its start.
    (window,) = row.windows
    planned = deployment_clock_windows(strategy, deploy_anchor=deployment.created_at)[0]
    assert (window.role, window.product_id, window.timeframe) == ("decision clock", "BTC-USD", "1h")
    assert window.starts_at == planned.starts_at
    assert window.warmup_bars == strategy.data_requirements.warmup_bars
    assert window.first_bar_at is not None
    assert window.first_bar_at > window.starts_at


async def test_decision_rows_record_each_window_from_its_start() -> None:
    """An established market's windows begin exactly at their planned starts (ADR 0140)."""
    strategy = with_convergence_warmup(reference_strategy())
    store = InMemoryExecutionStore()
    await create_deployment(
        store=store,
        publication_store=Catalog(strategy),
        strategy_fingerprint=strategy_fingerprint(strategy),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal("10000"),
        live_allowed=False,
        reference_watches=ReferenceWatchlist(store=await _watchlist(), provider="demo"),
    )
    journal = InMemoryDecisionJournalStore()
    with execution_audit_scope(InMemoryAuditEventStore()), decision_journal_scope(journal):
        await service._run_cycle(
            store=store,
            publication_store=Catalog(strategy),
            market_data=listed_history(),
            paper_broker=PaperBroker(),
            live_broker=None,
            quote_reader=None,
            risk_store=None,
        )
    (row,) = journal.rows()
    assert [(item.role, item.product_id, item.timeframe) for item in row.windows] == [
        ("decision clock", "ETH-USD", "1h"),
        ("reference btc", "BTC-USD", "1d"),
    ]
    assert all(item.first_bar_at == item.starts_at for item in row.windows)


def test_an_htf_window_must_also_cover_the_previous_mapped_bar_at_a_rollover() -> None:
    """5m decisions closing at 04:00 with a 2-bar 1h filter read hourly 01:00 to 03:00."""
    strategy = _startable_htf_strategy()
    htf_filter = strategy.htf_filter
    assert htf_filter is not None
    assert htf_filter.data_requirements.warmup_bars == 2
    warmup = strategy.data_requirements.warmup_bars
    last = datetime(2026, 10, 6, 3, 55, tzinfo=UTC)
    decision = tuple(
        candle(last - timedelta(minutes=5) * offset, "100") for offset in reversed(range(warmup))
    )
    hourly = {hour: candle(datetime(2026, 10, 6, hour, tzinfo=UTC), "30") for hour in (1, 2, 3)}
    assert windows_hold_warmup(strategy, decision, tuple(hourly.values()), None)
    assert not windows_hold_warmup(strategy, decision, (hourly[2], hourly[3]), None)


def test_an_empty_decision_window_never_holds_under_the_policy() -> None:
    """No decision bars is not a warmed-up window."""
    assert not windows_hold_warmup(_startable_htf_strategy(), (), (), None)


@pytest.mark.parametrize("failure", [ValueError, TypeError, RuntimeError])
async def test_a_window_failure_never_interrupts_the_cycle(
    monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    """Windows are evidence: if planning them fails, the bar is still evaluated and journaled."""

    def _broken(*_args: object, **_kwargs: object) -> tuple[ClockWindow, ...]:
        raise failure("planning failed")

    monkeypatch.setattr(windows_module, "deployment_clock_windows", _broken)
    strategy = create_template_strategy(product_id="BTC-USD", timeframe="1h")
    store = InMemoryExecutionStore()
    await create_deployment(
        store=store,
        publication_store=Catalog(strategy),
        strategy_fingerprint=strategy_fingerprint(strategy),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal("10000"),
        live_allowed=False,
    )
    journal = InMemoryDecisionJournalStore()
    with execution_audit_scope(InMemoryAuditEventStore()), decision_journal_scope(journal):
        await service._run_cycle(
            store=store,
            publication_store=Catalog(strategy),
            market_data=listed_history(),
            paper_broker=PaperBroker(),
            live_broker=None,
            quote_reader=None,
            risk_store=None,
        )
    (row,) = journal.rows()
    assert row.windows == ()
    assert row.skip_reason is not DecisionSkipReason.WARMUP
    (deployment,) = await store.list_deployments()
    after = await store.get_deployment(deployment.id)
    assert after.deployment.status is DeploymentStatus.RUNNING
    assert after.deployment.last_evaluated_bar == row.bar_starts_at


async def _reference_book(
    strategy: StrategyDefinition,
) -> tuple[InMemoryExecutionStore, DeploymentSnapshot]:
    """One running paper book of a reference strategy, its BTC 1d series watched."""
    store = InMemoryExecutionStore()
    created = await create_deployment(
        store=store,
        publication_store=Catalog(strategy),
        strategy_fingerprint=strategy_fingerprint(strategy),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal("10000"),
        live_allowed=False,
        reference_watches=ReferenceWatchlist(store=await _watchlist(), provider="demo"),
    )
    return store, await store.get_deployment(created.id)


async def test_a_stopped_book_passes_its_signal_exit_windows_to_the_journal() -> None:
    """Managed-stopped signal exits are evaluated bars: their windows reach the journal."""
    strategy = with_convergence_warmup(reference_strategy())
    store, snapshot = await _reference_book(strategy)
    decision = hourly_bars(datetime(2026, 10, 6, tzinfo=UTC), 3)
    htf: tuple[Candle, ...] = ()
    references = {"btc": daily_bars(datetime(2026, 10, 1, tzinfo=UTC), "100", "101")}
    seen: dict[str, object] = {}

    async def _signal_windows(
        strategy: StrategyDefinition, *, product_id: str, deploy_anchor: datetime
    ) -> tuple[tuple[Candle, ...], Mapping[str, Sequence[Candle]], Mapping[str, Sequence[Candle]]]:
        del strategy, product_id, deploy_anchor
        return htf, {}, references

    async def _journal(
        snapshot: DeploymentSnapshot,
        *,
        strategy: StrategyDefinition,
        product: MarketProduct,
        candles: tuple[Candle, ...],
        advance: Callable[[], Awaitable[DeploymentSnapshot]],
        require_activity: bool,
        htf_candles: Sequence[Candle] = (),
        indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
        reference_candles: Mapping[str, Sequence[Candle]] | None = None,
    ) -> None:
        del snapshot, strategy, product, candles, advance, require_activity
        seen.update(
            htf_candles=htf_candles,
            indicator_timeframe_candles=indicator_timeframe_candles,
            reference_candles=reference_candles,
        )

    await stopped_module._maintain_strategy_book(
        snapshot,
        strategy=strategy,
        context=stopped_module._ExitContext(product=_eth(), candles=decision),
        store=store,
        broker=PaperBroker(),
        load_signal_windows=_signal_windows,
        journal_strategy_bar=_journal,
        deploy_anchor=snapshot.deployment.created_at,
    )
    assert seen["reference_candles"] == references
    assert seen["htf_candles"] == htf


async def test_the_stopped_journal_records_the_windows_it_was_given() -> None:
    """The worker's stopped-bar journal turns those windows into the row's evidence."""
    strategy = with_convergence_warmup(reference_strategy())
    _store, snapshot = await _reference_book(strategy)
    decision = hourly_bars(datetime(2026, 10, 6, tzinfo=UTC), 3)
    references = {"btc": daily_bars(datetime(2026, 10, 1, tzinfo=UTC), "100", "101")}
    journal = InMemoryDecisionJournalStore()

    async def _advance() -> DeploymentSnapshot:
        return snapshot

    with decision_journal_scope(journal):
        await stopped_step._journal_stopped_bar(
            snapshot,
            strategy=strategy,
            product=_eth(),
            candles=decision,
            advance=_advance,
            require_activity=False,
            reference_candles=references,
        )
    (row,) = journal.rows()
    assert [(item.role, item.first_bar_at) for item in row.windows] == [
        ("decision clock", decision[0].starts_at),
        ("reference btc", references["btc"][0].starts_at),
    ]
