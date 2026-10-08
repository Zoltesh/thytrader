"""Paper/live reference-instrument gates (ADR 0096).

A decision bar may open risk only when every reference series has a contiguous
closed-bar window ending with the reference bar that closed last at or before the
decision close and covering its warmup. Otherwise entries are skipped with
``REFERENCE_DATA_STALE`` / ``REFERENCE_DATA_MISSING``. Starting a reference strategy
requires each reference series on the enabled market-data watchlist.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from typing import cast

import pytest

from tests.execution.decision_support import Catalog
from tests.strategies.reference_support import (
    GOLDEN,
    daily_bars,
    hourly_bars,
    reference_payload,
    reference_strategy,
)
from thytrader.evaluation.trace import EntryConditionOutcome
from thytrader.execution.decision_journal import decision_journal_scope
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import (
    BarDecision,
    ConditionComparisonTrace,
    ConditionGroupTrace,
    DecisionOutcome,
    DecisionSkipReason,
)
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.paper import PaperBroker
from thytrader.execution.references import reference_gate
from thytrader.execution.service import (
    ReferenceWatchlist,
    create_deployment,
    reference_watch_lookback_hours,
)
from thytrader.execution.signals import evaluate_latest_entry_evidence
from thytrader.execution_worker.service import _journaled_bar
from thytrader.market_data.models import Candle, CandleInterval, MarketProduct
from thytrader.market_data.watchlist import (
    DisabledMarketDataWatchlistStore,
    InMemoryMarketDataWatchlistStore,
    MarketDataWatchTarget,
)
from thytrader.strategies.models import (
    StrategyDefinition,
    reference_data_requirements,
    strategy_fingerprint,
)
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, ExecutionConflictError

_DECISION_CLOSE = datetime(2026, 7, 11, 3, tzinfo=UTC)
_DAY = datetime(2026, 7, 8, tzinfo=UTC)


def _eth() -> MarketProduct:
    """ETH-USD venue increments."""
    return MarketProduct(
        product_id="ETH-USD",
        base_currency="ETH",
        quote_currency="USD",
        price_increment=Decimal("0.01"),
        base_increment=Decimal("0.00000001"),
        quote_increment=Decimal("0.01"),
        base_min_size=Decimal("0.001"),
        quote_min_size=Decimal("1"),
        trading_enabled=True,
    )


def test_gate_is_open_when_the_last_closed_reference_bar_and_warmup_are_present() -> None:
    """07-09 and 07-10 cover the 2-bar warmup at 07-11 03:00; the in-progress 07-11 is ignored."""
    bars = daily_bars(_DAY, "100", "100", "200", "1")
    assert (
        reference_gate(reference_strategy(), {"btc": bars}, decision_close=_DECISION_CLOSE) is None
    )


def test_gate_reports_stale_when_the_needed_bar_has_not_arrived() -> None:
    """Bars through 07-09 only: the 07-10 bar the decision needs is missing at the head."""
    bars = daily_bars(_DAY, "100", "100")
    gate = reference_gate(reference_strategy(), {"btc": bars}, decision_close=_DECISION_CLOSE)
    assert gate is not None
    assert gate.reason is DecisionSkipReason.REFERENCE_DATA_STALE
    assert "btc (BTC-USD 1d)" in gate.detail
    assert "2026-07-09T00:00:00Z" in gate.detail
    assert "2026-07-10T00:00:00Z" in gate.detail


@pytest.mark.parametrize(
    ("bars", "fragment"),
    [
        ((), "no closed bars"),
        (daily_bars(datetime(2026, 7, 11, tzinfo=UTC), "1"), "no closed bars"),
        (daily_bars(datetime(2026, 7, 10, tzinfo=UTC), "200"), "1 of 2 closed bars"),
    ],
)
def test_gate_reports_missing_without_usable_history(
    bars: tuple[Candle, ...], fragment: str
) -> None:
    """No bars, only an in-progress bar, or too little warmup history are missing data."""
    gate = reference_gate(reference_strategy(), {"btc": bars}, decision_close=_DECISION_CLOSE)
    assert gate is not None
    assert gate.reason is DecisionSkipReason.REFERENCE_DATA_MISSING
    assert fragment in gate.detail


def test_reference_free_strategies_are_never_gated() -> None:
    """A document without references has nothing to gate."""
    raw = (GOLDEN / "reference_strategy_v1.json").read_bytes()
    plain = StrategyDefinition.model_validate_json(raw)
    assert reference_gate(plain, {}, decision_close=_DECISION_CLOSE) is None


def test_live_evaluation_drops_in_progress_reference_bars_and_leaves_missing_ones_undefined() -> (
    None
):
    """Paper/live evidence reads the last closed daily bar only; no bars mean UNDEFINED."""
    strategy = reference_strategy()
    candles = hourly_bars(_DECISION_CLOSE - timedelta(hours=4), 4)
    poisoned = daily_bars(_DAY, "100", "100", "200", "99999")
    evaluation = evaluate_latest_entry_evidence(
        strategy, candles, reference_candles={"btc": poisoned}
    )
    assert evaluation.outcome is EntryConditionOutcome.MATCHED
    assert evaluation.current["btc_close"] == Decimal(200)
    blind = evaluate_latest_entry_evidence(strategy, candles)
    assert blind.outcome is EntryConditionOutcome.UNDEFINED
    assert blind.current["btc_close"] is None


async def _watchlist(*, enabled: bool = True) -> InMemoryMarketDataWatchlistStore:
    """A watchlist holding the BTC-USD 1d reference series."""
    store = InMemoryMarketDataWatchlistStore()
    await store.upsert(
        MarketDataWatchTarget(
            provider="demo",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_DAY,
            lookback_hours=2424,
            enabled=enabled,
            updated_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
    )
    return store


async def _start(
    definition: StrategyDefinition, watches: ReferenceWatchlist | None
) -> InMemoryExecutionStore:
    """Start one paper book and return its store."""
    store = InMemoryExecutionStore()
    await create_deployment(
        store=store,
        publication_store=Catalog(definition),
        strategy_fingerprint=strategy_fingerprint(definition),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal("10000"),
        live_allowed=False,
        reference_watches=watches,
    )
    return store


@pytest.mark.anyio
async def test_start_is_refused_until_every_reference_series_is_watched() -> None:
    """Unwatched or disabled references refuse the start and name watch-add."""
    definition = reference_strategy()
    empty = ReferenceWatchlist(store=InMemoryMarketDataWatchlistStore(), provider="demo")
    with pytest.raises(ExecutionConflictError) as caught:
        await _start(definition, empty)
    message = str(caught.value)
    assert "btc (BTC-USD 1d)" in message
    assert "Nothing was started" in message
    expected_hours = reference_watch_lookback_hours(reference_data_requirements(definition)[0])
    assert (
        "uv run thytrader-data watch-add --product-id BTC-USD --timeframe 1d "
        f"--lookback-hours {expected_hours} --confirm"
    ) in message
    disabled = ReferenceWatchlist(store=await _watchlist(enabled=False), provider="demo")
    with pytest.raises(ExecutionConflictError, match="enabled market-data watchlist"):
        await _start(definition, disabled)


@pytest.mark.anyio
async def test_start_fails_closed_without_a_readable_watchlist() -> None:
    """No watchlist, or an unavailable one, never starts a reference strategy."""
    definition = reference_strategy()
    with pytest.raises(ExecutionConflictError, match="unavailable"):
        await _start(definition, None)
    unavailable = ReferenceWatchlist(store=DisabledMarketDataWatchlistStore(), provider="demo")
    with pytest.raises(ExecutionConflictError, match="unavailable"):
        await _start(definition, unavailable)


@pytest.mark.anyio
async def test_start_succeeds_when_the_reference_is_watched() -> None:
    """An enabled watch on the reference series lets the bot start."""
    definition = reference_strategy()
    store = await _start(definition, ReferenceWatchlist(store=await _watchlist(), provider="demo"))
    (deployment,) = await store.list_deployments()
    assert deployment.product_id == "ETH-USD"


def test_suggested_lookback_covers_reference_warmup_and_is_capped() -> None:
    """EMA(100) on 1d suggests 101 days; tiny warmups still suggest a 7-day watch."""
    payload = reference_payload()
    indicators = cast("list[dict[str, object]]", payload["indicators"])
    indicators[2]["parameters"] = {"period": 100}
    (requirement,) = reference_data_requirements(StrategyDefinition.model_validate(payload))
    assert reference_watch_lookback_hours(requirement) == 101 * 24
    (small,) = reference_data_requirements(reference_strategy(reference_timeframe="1h"))
    assert reference_watch_lookback_hours(small) == 168


async def _journaled(
    definition: StrategyDefinition,
    *,
    reference_candles: dict[str, tuple[Candle, ...]],
    stale: bool,
) -> tuple[InMemoryExecutionStore, BarDecision]:
    """Process the 02:00 decision bar like the worker and return its decision row."""
    store = await _start(definition, ReferenceWatchlist(store=await _watchlist(), provider="demo"))
    (deployment,) = await store.list_deployments()
    snapshot = await store.get_deployment(deployment.id)
    window = hourly_bars(_DECISION_CLOSE - timedelta(hours=4), 4)
    gate = reference_gate(definition, reference_candles, decision_close=_DECISION_CLOSE)
    assert (gate is not None) is stale
    journal = InMemoryDecisionJournalStore()
    with decision_journal_scope(journal):
        await _journaled_bar(
            snapshot,
            strategy=definition,
            product_id="ETH-USD",
            candle=window[-1],
            allow_new_entries=gate is None,
            reference_gate=gate,
            advance=partial(
                process_closed_bar,
                snapshot,
                strategy=definition,
                product=_eth(),
                candles=window,
                broker=PaperBroker(),
                store=store,
                reference_candles=reference_candles,
                allow_new_entries=gate is None,
            ),
        )
    (row,) = journal.rows()
    return store, row


@pytest.mark.anyio
async def test_stale_reference_skips_the_entry_and_journals_the_reason() -> None:
    """The bar is processed (exits run) but no entry is attempted; the row says why."""
    definition = reference_strategy()
    store, row = await _journaled(
        definition, reference_candles={"btc": daily_bars(_DAY, "100", "100")}, stale=True
    )
    assert row.outcome is DecisionOutcome.SKIPPED
    assert row.skip_reason is DecisionSkipReason.REFERENCE_DATA_STALE
    assert row.reason_code == "REFERENCE_DATA_STALE"
    assert row.summary.startswith("Skipped: reference data stale — reference btc (BTC-USD 1d)")
    (deployment,) = await store.list_deployments()
    snapshot = await store.get_deployment(deployment.id)
    assert snapshot.intents == ()
    assert snapshot.deployment.last_evaluated_bar == datetime(2026, 7, 11, 2, tzinfo=UTC)


@pytest.mark.anyio
async def test_fresh_reference_values_appear_in_the_decision_row() -> None:
    """The rule trace labels reference operands ``BTC · close [1d]`` with the values read."""
    definition = reference_strategy()
    _store, row = await _journaled(
        definition,
        reference_candles={"btc": daily_bars(_DAY, "100", "100", "200", "1")},
        stale=False,
    )
    assert row.skip_reason is None
    assert row.rule is not None
    entry = row.rule.entry
    assert isinstance(entry, ConditionGroupTrace)
    comparison = entry.children[0]
    assert isinstance(comparison, ConditionComparisonTrace)
    assert comparison.left.label == "BTC · Close [1d]"
    assert comparison.left.value == "200"
    assert comparison.right.label == "BTC · SMA(2) [1d]"
    assert comparison.right.value == "150"
