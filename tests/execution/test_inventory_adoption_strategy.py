"""Live strategy start that adopts coins already held at Coinbase (ADR 0124)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.adoption_support import balance, balance_reader
from tests.execution.decision_support import Catalog, strategy
from tests.execution.test_inventory_adoption_service import _InhibitedLatch, _Venue
from thytrader.execution.adoption_strategy import (
    ADOPTION_STRATEGY_UNSUPPORTED,
    StrategyAdoptionVenue,
    require_adoptable_strategy,
    start_with_adoption,
)
from thytrader.execution.closed_windows import _closed_window
from thytrader.execution.loop import maintain_open_inventory
from thytrader.execution.paper import PaperBroker
from thytrader.execution.signals import latest_atr
from thytrader.execution_worker.service import _process_one
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import Candle
from thytrader.market_data.service import MarketDataService
from thytrader.memory.store import InMemoryExperientialMemoryStore
from thytrader.risk.models import CapitalAllocation, compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.trading.adoption_write import ADOPTION_QUANTITY_UNAVAILABLE, AdoptionRefusedError
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.memory_adoption import InMemoryInventoryAdoptionStore
from thytrader.trading.models import (
    DeploymentKind,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionConflictError,
    IntentPurpose,
    OrderKind,
    OrderSide,
    RuntimePhase,
)
from thytrader.trading.sizing import SizedEntry, size_entry_or_skip

if TYPE_CHECKING:
    from thytrader.exchanges.fees import FeeProfile
    from thytrader.exchanges.models import ExchangeBalance

pytestmark = pytest.mark.anyio

_WIDE = {
    "version": 2,
    "max_portfolio_exposure_fraction": "1",
    "per_product_max_exposure_fraction": "1",
}


class _Account:
    """A live account double holding 0.05 BTC and 100000 USD."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """The scripted balances."""
        return (balance("BTC", "0.05"), balance("USD", "100000"))

    async def get_fee_profile(self) -> FeeProfile:
        """No fee evidence: sizing does not run for an adopted book."""
        raise RuntimeError("no fee profile in tests")


@dataclass
class _Harness:
    """A memory-backed live account and a published wide policy."""

    definition: StrategyDefinition = field(default_factory=strategy)
    store: InMemoryExecutionStore = field(default_factory=InMemoryExecutionStore)
    venue: _Venue = field(default_factory=_Venue)
    market: MarketDataService = field(default_factory=lambda: MarketDataService(DemoMarketData()))
    risk: InMemoryRiskPolicyStore = field(default_factory=InMemoryRiskPolicyStore)
    btc: str = "0.05"

    async def start(self, quantity: Decimal | None = Decimal("0.04")) -> DeploymentSnapshot:
        """Start the template strategy live, adopting ``quantity`` BTC."""
        return await start_with_adoption(
            store=self.store,
            publication_store=Catalog(self.definition),
            strategy_fingerprint=strategy_fingerprint(self.definition),
            quantity=quantity,
            live_allowed=True,
            risk_store=self.risk,
            venue=StrategyAdoptionVenue(
                adoption_store=InMemoryInventoryAdoptionStore(self.store),
                market_data=self.market,
                read_balances=balance_reader(balance("BTC", self.btc), balance("USD", "100000")),
                live_quote_cash=Decimal(100000),
                memory_store=InMemoryExperientialMemoryStore(),
            ),
        )


async def _harness(definition: StrategyDefinition | None = None, **policy: object) -> _Harness:
    """A harness with a published policy (wide unless overridden)."""
    harness = _Harness() if definition is None else _Harness(definition=definition)
    update = {**_WIDE, **policy}
    await harness.risk.publish(compiled_default_risk_policy().model_copy(update=update))
    return harness


def _with_exits(*, signal_exit: bool = False, short: bool = False) -> StrategyDefinition:
    """The template strategy with an always-true signal exit, or on the short side."""
    payload = strategy().model_dump(mode="python", by_alias=True)
    if signal_exit:
        payload["exits"]["signal_exit"] = {
            "when": {
                "all": [
                    {
                        "left": {"indicator": "rsi"},
                        "operator": "greater_than_or_equal",
                        "right": {"literal": "0"},
                    }
                ]
            }
        }
    if short:
        payload["entry"]["side"] = "short"
    return StrategyDefinition.model_validate(payload)


async def test_the_bot_starts_holding_the_coins_at_the_workers_levels() -> None:
    """Stop and target equal what the worker sizes on the same anchored window."""
    harness = await _harness()
    started = await harness.start()
    deployment = started.deployment
    assert deployment.kind is DeploymentKind.STRATEGY
    assert deployment.status is DeploymentStatus.RUNNING
    assert deployment.phase is RuntimePhase.OPEN
    position = started.position
    assert position is not None and position.quantity == Decimal("0.04")
    product, window, _expected = await _closed_window(
        harness.market, harness.definition, deploy_anchor=deployment.created_at
    )
    atr = latest_atr(harness.definition, window)
    assert atr is not None
    worker = size_entry_or_skip(
        strategy=harness.definition,
        cash=Decimal(10**9),
        entry_price=window[-1].close,
        atr=atr,
        product=product,
    )
    assert isinstance(worker, SizedEntry)
    assert position.stop_price == worker.stop_price
    assert position.target_price == worker.target_price
    assert position.entry_price == window[-1].close
    assert deployment.last_evaluated_bar == window[-1].starts_at
    assert deployment.performance_capital_quote == Decimal("0.04") * window[-1].close
    assert [intent.purpose for intent in started.intents] == [IntentPurpose.ADOPTION]
    assert harness.venue.placed == []


async def test_performance_capital_is_at_least_the_allocation() -> None:
    """max(allocation, adopted notional): a larger allocation stays the basis."""
    definition = strategy()
    harness = await _harness(
        definition,
        allocations=(
            CapitalAllocation(strategy_id=definition.strategy_id, allocated_quote="90000"),
        ),
    )
    started = await harness.start()
    assert started.deployment.performance_capital_quote == Decimal(90000)


async def test_a_refused_adoption_leaves_no_bot_behind() -> None:
    """Book and adoption commit together; a refused adoption inserts no FLAT running bot."""
    harness = await _harness()
    with pytest.raises(AdoptionRefusedError) as refused:
        await harness.start(Decimal("0.5"))
    assert refused.value.code == ADOPTION_QUANTITY_UNAVAILABLE
    assert harness.store.deployments == {} and harness.store.intents == {}


async def test_a_disarmed_fleet_refuses_the_start() -> None:
    """Strategy adoption respects the fleet latch like any live start."""
    harness = await _harness()
    harness.store.bind_entry_gate(_InhibitedLatch())
    with pytest.raises(ExecutionConflictError, match="ENTRY_INHIBITED"):
        await harness.start()
    assert harness.store.deployments == {}


async def test_short_side_strategies_are_unsupported() -> None:
    """v1 adopts into long-side, single-instrument strategies only."""
    with pytest.raises(AdoptionRefusedError) as refused:
        require_adoptable_strategy(_with_exits(short=True))
    assert refused.value.code == ADOPTION_STRATEGY_UNSUPPORTED
    harness = await _harness(_with_exits(short=True))
    with pytest.raises(AdoptionRefusedError):
        await harness.start()
    assert harness.store.deployments == {}


async def test_the_next_worker_cycle_places_the_strategy_protection() -> None:
    """The real worker step finds an OPEN book and rests the stop and target at the venue."""
    harness = await _harness()
    started = await harness.start()
    position = started.position
    assert position is not None
    await _process_one(
        deployment_id=started.deployment.id,
        store=harness.store,
        publication_store=Catalog(harness.definition),
        market_data=harness.market,
        paper_broker=PaperBroker(),
        live_broker=harness.venue,
        quote_reader=_Account(),
        risk_policy=compiled_default_risk_policy().model_copy(update=_WIDE),
        portfolio=(),
        user_feed_store=None,
        memory_store=None,
    )
    assert harness.venue.placed == [(OrderKind.TRIGGER_BRACKET, OrderSide.SELL, Decimal("0.04"))]
    after = await harness.store.get_deployment(started.deployment.id)
    assert after.deployment.status is DeploymentStatus.RUNNING
    assert after.position is not None and after.position.stop_price == position.stop_price


async def test_a_signal_exit_sells_the_adopted_lot() -> None:
    """The strategy's exits manage the adopted coins: a matched signal exit sells them."""
    harness = await _harness(_with_exits(signal_exit=True))
    started = await harness.start()
    product, window, _expected = await _closed_window(
        harness.market, harness.definition, deploy_anchor=started.deployment.created_at
    )
    step = window[-1].starts_at - window[-2].starts_at
    later: list[Candle] = []
    previous = window[-1]
    # The bar holding the adoption instant, then the first bar fully after it.
    for _bar in range(2):
        previous = Candle(
            starts_at=previous.starts_at + step,
            open=previous.close,
            high=previous.close + Decimal(100),
            low=previous.close - Decimal(50),
            close=previous.close + Decimal(10),
            volume=Decimal(5),
        )
        later.append(previous)
    await maintain_open_inventory(
        started,
        strategy=harness.definition,
        product=product,
        candles=(*window, *later),
        broker=harness.venue,
        store=harness.store,
    )
    sells = [item for item in harness.venue.placed if item[0] is OrderKind.MARKETABLE]
    assert sells == [(OrderKind.MARKETABLE, OrderSide.SELL, Decimal("0.04"))]
    after = await harness.store.get_deployment(started.deployment.id)
    assert after.position is None or after.position.signal_exit_bar is not None
