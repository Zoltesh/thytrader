"""Real worker cycles with reference instruments on hermetic demo candles (ADR 0096).

The worker loads each reference series every cycle. Fresh references let the entry rule
run and the decision row shows the reference values; a stale or missing reference skips
new entries with an explicit reason while the bar is still processed and the bot stays
running. Multi-instrument documents apply one gate to every covered product.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.execution.decision_support import Catalog
from tests.strategies.reference_support import reference_payload, reference_strategy
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.decision_journal import decision_journal_scope
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import (
    ConditionComparisonTrace,
    ConditionGroupTrace,
    DecisionSkipReason,
)
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import DeploymentMode, DeploymentStatus
from thytrader.execution.paper import PaperBroker
from thytrader.execution.service import ReferenceWatchlist, create_deployment
from thytrader.execution_worker.service import _run_cycle
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import CandleInterval
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.service import MarketDataService
from thytrader.market_data.watchlist import InMemoryMarketDataWatchlistStore, MarketDataWatchTarget
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint

if TYPE_CHECKING:
    from thytrader.execution.decisions import BarDecision
    from thytrader.market_data.models import CandleRangeReport


class _StaleReference(DemoMarketData):
    """Demo candles whose BTC-USD daily series lags one closed bar behind."""

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Drop the newest closed BTC-USD 1d bar; every other series is complete."""
        report = await super().get_historical_range(product_id, interval, starts_at, ends_at, now)
        if product_id != "BTC-USD" or interval is not CandleInterval.ONE_DAY:
            return report
        return analyze_range(report.quality.candles[:-1], interval, starts_at, ends_at, now)


async def _book(definition: StrategyDefinition) -> InMemoryExecutionStore:
    """Start one paper book with every reference series watched."""
    watchlist = InMemoryMarketDataWatchlistStore()
    for reference in definition.data_requirements.reference_instruments:
        await watchlist.upsert(
            MarketDataWatchTarget(
                provider="demo",
                product_id=reference.product_id,
                timeframe=CandleInterval(reference.timeframe),
                lookback_hours=2424,
                enabled=True,
                updated_at=datetime(2026, 10, 1, tzinfo=UTC),
            )
        )
    store = InMemoryExecutionStore()
    await create_deployment(
        store=store,
        publication_store=Catalog(definition),
        strategy_fingerprint=strategy_fingerprint(definition),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal("10000"),
        live_allowed=False,
        reference_watches=ReferenceWatchlist(store=watchlist, provider="demo"),
    )
    return store


async def _cycle(
    definition: StrategyDefinition, provider: DemoMarketData
) -> tuple[InMemoryExecutionStore, tuple[BarDecision, ...]]:
    """Run one real worker cycle and return the store and journal rows."""
    store = await _book(definition)
    journal = InMemoryDecisionJournalStore()
    with execution_audit_scope(InMemoryAuditEventStore()), decision_journal_scope(journal):
        await _run_cycle(
            store=store,
            publication_store=Catalog(definition),
            market_data=MarketDataService(provider),
            paper_broker=PaperBroker(),
            live_broker=None,
            quote_reader=None,
            risk_store=None,
        )
    return store, journal.rows()


def _first_comparison(row: BarDecision) -> ConditionComparisonTrace:
    """The entry rule's first comparison trace."""
    assert row.rule is not None
    entry = row.rule.entry
    assert isinstance(entry, ConditionGroupTrace)
    comparison = entry.children[0]
    assert isinstance(comparison, ConditionComparisonTrace)
    return comparison


@pytest.mark.anyio
async def test_fresh_reference_runs_the_entry_rule_and_shows_reference_values() -> None:
    """Demo BTC dailies rise, so the gate is open and the rule reads BTC values."""
    store, rows = await _cycle(reference_strategy(), DemoMarketData())
    (row,) = rows
    assert row.skip_reason not in {
        DecisionSkipReason.REFERENCE_DATA_STALE,
        DecisionSkipReason.REFERENCE_DATA_MISSING,
    }
    comparison = _first_comparison(row)
    assert comparison.left.label == "BTC · Close [1d]"
    assert comparison.left.value is not None
    assert comparison.right.label == "BTC · SMA(2) [1d]"
    (deployment,) = await store.list_deployments()
    assert deployment.last_signal == "matched"


@pytest.mark.anyio
async def test_stale_reference_skips_entries_and_keeps_the_bot_running() -> None:
    """A lagging BTC daily series journals REFERENCE_DATA_STALE and creates no intent."""
    store, rows = await _cycle(reference_strategy(), _StaleReference())
    (row,) = rows
    assert row.skip_reason is DecisionSkipReason.REFERENCE_DATA_STALE
    assert row.reason_code == "REFERENCE_DATA_STALE"
    (deployment,) = await store.list_deployments()
    snapshot = await store.get_deployment(deployment.id)
    assert snapshot.deployment.status is DeploymentStatus.RUNNING
    assert snapshot.intents == ()
    assert snapshot.deployment.last_evaluated_bar == row.bar_starts_at


@pytest.mark.anyio
async def test_missing_reference_series_skips_entries_with_missing_reason() -> None:
    """A reference product the provider cannot serve journals REFERENCE_DATA_MISSING."""
    definition = reference_strategy(reference_product="ADA-USD")
    store, rows = await _cycle(definition, DemoMarketData())
    (row,) = rows
    assert row.skip_reason is DecisionSkipReason.REFERENCE_DATA_MISSING
    assert "ADA-USD 1d" in row.summary
    (deployment,) = await store.list_deployments()
    assert (await store.get_deployment(deployment.id)).intents == ()


@pytest.mark.anyio
async def test_multi_instrument_documents_gate_every_covered_product_together() -> None:
    """ETH-USD and SOL-USD share one stale BTC gate on the shared bar."""
    payload = reference_payload()
    payload["additional_instruments"] = [
        {"product_id": "SOL-USD", "base_currency": "SOL", "quote_currency": "USD"}
    ]
    definition = StrategyDefinition.model_validate(payload)
    _store, rows = await _cycle(definition, _StaleReference())
    assert sorted(row.product_id for row in rows) == ["ETH-USD", "SOL-USD"]
    assert {row.skip_reason for row in rows} == {DecisionSkipReason.REFERENCE_DATA_STALE}
    _store, fresh = await _cycle(definition, DemoMarketData())
    assert {_first_comparison(row).left.label for row in fresh} == {"BTC · Close [1d]"}
