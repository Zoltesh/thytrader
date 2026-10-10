"""BTC-beta cap evidence reaches every worker entry path (ADR 0125).

Each test runs the real worker (``_run_cycle`` or ``_process_one``), which binds the risk
market-data scope, so a missing binding would leave the evidence unloaded and deny entries.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from tests.beta_support import DailyBetaProvider, DemoWithDailyBeta, beta_policy
from tests.execution.decision_support import Catalog, paper_book, strategy
from thytrader.execution.decision_journal import decision_journal_scope
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.paper import PaperBroker
from thytrader.execution.service import PortfolioSleeveStart, create_deployment
from thytrader.execution_worker.service import _process_one, _risk_snapshots, _run_cycle
from thytrader.risk.beta_evidence import DEFAULT_BETA_CACHE
from thytrader.risk.portfolio_limits import PortfolioRiskBook
from thytrader.risk.portfolio_scope import portfolio_risk_scope
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    IntentPurpose,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from thytrader.risk.models import RiskPolicyDefinition

pytestmark = pytest.mark.anyio

_ALWAYS = {
    "all": [
        {"left": {"literal": "1"}, "operator": "greater_than_or_equal", "right": {"literal": "0"}}
    ]
}


@pytest.fixture(autouse=True)
def _fresh_beta_cache() -> Iterator[None]:
    """The worker uses the process-wide β cache; isolate it per test."""
    DEFAULT_BETA_CACHE.clear()
    yield
    DEFAULT_BETA_CACHE.clear()


def _always_entry(
    product_id: str = "ETH-USD", *, additional: tuple[str, ...] = ()
) -> StrategyDefinition:
    """The template strategy on ``product_id`` whose entry rule always matches."""
    payload = strategy(when=_ALWAYS).model_dump(mode="python", by_alias=True)
    # Demo prices are near 100,000: allow a notional above every demo base minimum.
    payload["sizing"]["max_quote_notional"] = "500"
    payload["instrument"] = {
        "product_id": product_id,
        "base_currency": product_id.split("-", maxsplit=1)[0],
        "quote_currency": "USD",
    }
    if additional:
        payload["additional_instruments"] = [
            {"product_id": item, "base_currency": item.split("-")[0], "quote_currency": "USD"}
            for item in additional
        ]
        payload["portfolio_limits"]["max_concurrent_positions"] = 1 + len(additional)
    return StrategyDefinition.model_validate(payload)


async def _risk(policy: RiskPolicyDefinition) -> InMemoryRiskPolicyStore:
    """A risk store with ``policy`` published."""
    store = InMemoryRiskPolicyStore()
    await store.publish(policy)
    return store


async def _cycle(
    definition: StrategyDefinition,
    store: InMemoryExecutionStore,
    provider: DemoWithDailyBeta,
    policy: RiskPolicyDefinition,
    journal: InMemoryDecisionJournalStore | None = None,
) -> None:
    """Run one real worker cycle."""
    with decision_journal_scope(journal):
        await _run_cycle(
            store=store,
            publication_store=Catalog(definition),
            market_data=provider.service(),
            paper_broker=PaperBroker(),
            live_broker=None,
            quote_reader=None,
            risk_store=await _risk(policy),
        )


def _entry_products(snapshot: DeploymentSnapshot) -> list[str]:
    """Products with an ENTRY intent on the book."""
    return sorted(
        intent.product_id or snapshot.deployment.product_id
        for intent in snapshot.intents
        if intent.purpose is IntentPurpose.ENTRY
    )


async def test_strategy_entry_reads_beta_through_the_worker_scope() -> None:
    """A set cap reads ETH and BTC daily history and the entry is admitted."""
    definition = _always_entry()
    store, snapshot = await paper_book(definition)
    provider = DemoWithDailyBeta(DailyBetaProvider(betas={"ETH-USD": 1.2}))

    await _cycle(definition, store, provider, beta_policy())

    after = await store.get_deployment(snapshot.deployment.id)
    assert _entry_products(after) == ["ETH-USD"]
    assert sorted(set(provider.daily.requested_products())) == ["BTC-USD", "ETH-USD"]


async def test_unset_cap_reads_no_beta_history_in_a_worker_cycle() -> None:
    """Without β fields the same cycle enters and makes zero β reads."""
    definition = _always_entry()
    store, snapshot = await paper_book(definition)
    provider = DemoWithDailyBeta(DailyBetaProvider(betas={"ETH-USD": 1.2}))

    await _cycle(definition, store, provider, beta_policy(fraction=None))

    after = await store.get_deployment(snapshot.deployment.id)
    assert _entry_products(after) == ["ETH-USD"]
    assert provider.daily.requests == []


async def test_strategy_entry_without_beta_history_is_skipped_not_paused() -> None:
    """Unreadable ETH history denies the entry with BTC_BETA_UNAVAILABLE; the bot runs on."""
    definition = _always_entry()
    store, snapshot = await paper_book(definition)
    provider = DemoWithDailyBeta(DailyBetaProvider(betas={"ETH-USD": 1.2}, failing={"ETH-USD"}))
    journal = InMemoryDecisionJournalStore()

    await _cycle(definition, store, provider, beta_policy(), journal)

    after = await store.get_deployment(snapshot.deployment.id)
    assert _entry_products(after) == []
    assert after.deployment.status is DeploymentStatus.RUNNING
    [row] = journal.rows()
    assert row.risk is not None
    assert row.risk.reason_code == "BTC_BETA_UNAVAILABLE"
    assert "ETH-USD vs BTC-USD: fetch_failed" in row.risk.detail


async def test_lockstep_entries_read_beta_through_the_worker_scope() -> None:
    """A multi-instrument document admits each product's entry with its own β."""
    definition = _always_entry("BTC-USD", additional=("ETH-USD",))
    store, snapshot = await paper_book(definition)
    provider = DemoWithDailyBeta(DailyBetaProvider(betas={"ETH-USD": 1.2}))

    await _cycle(definition, store, provider, beta_policy())

    after = await store.get_deployment(snapshot.deployment.id)
    assert _entry_products(after) == ["BTC-USD", "ETH-USD"]
    assert "ETH-USD" in provider.daily.requested_products()


async def test_portfolio_sleeve_entry_reads_beta_through_the_worker_scope() -> None:
    """A sleeve book, processed by the worker with its portfolio bound, is β-capped too."""
    definition = _always_entry()
    store = InMemoryExecutionStore()
    created = await create_deployment(
        store=store,
        publication_store=Catalog(definition),
        strategy_fingerprint=strategy_fingerprint(definition),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal(10000),
        live_allowed=False,
        portfolio_sleeve=PortfolioSleeveStart(
            portfolio_id=_SLEEVE_PORTFOLIO.portfolio_id, allocated_capital=Decimal(10000)
        ),
    )
    provider = DemoWithDailyBeta(DailyBetaProvider(betas={"ETH-USD": 1.2}))
    deployments = await store.list_deployments()

    with portfolio_risk_scope(_SLEEVE_PORTFOLIO):
        await _process_one(
            deployment_id=created.id,
            store=store,
            publication_store=Catalog(definition),
            market_data=provider.service(),
            paper_broker=PaperBroker(),
            live_broker=None,
            quote_reader=None,
            risk_policy=beta_policy(),
            portfolio=await _risk_snapshots(store, deployments),
            user_feed_store=None,
            memory_store=None,
        )

    after = await store.get_deployment(created.id)
    assert after.deployment.portfolio_id == _SLEEVE_PORTFOLIO.portfolio_id
    assert _entry_products(after) == ["ETH-USD"]
    assert "ETH-USD" in provider.daily.requested_products()


_SLEEVE_PORTFOLIO = PortfolioRiskBook(
    portfolio_id=UUID("01978a3e-5f2c-7d10-b3a4-00000000b0b0"),
    capital=Decimal(10000),
    max_total_exposure_fraction=Decimal(1),
    max_per_asset_fraction=Decimal(1),
)
