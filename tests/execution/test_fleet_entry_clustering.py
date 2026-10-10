"""Fleet entry clustering cap in the closed-bar loop (ADR 0125)."""

from datetime import timedelta
from decimal import Decimal

import pytest

from tests.execution.test_discretionary import _request as _discretionary_request
from tests.execution.test_loop import (
    _always_entry_strategy,
    _candles,
    _product,
    _running_snapshot,
)
from thytrader.execution.discretionary import place_discretionary_order
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.paper import PaperBroker
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import Candle
from thytrader.market_data.service import MarketDataService
from thytrader.risk.models import RiskPolicyDefinition, compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentStatus,
    ExecutionConflictError,
    IntentPurpose,
    OrderStatus,
    RuntimePhase,
)


def _clustered_policy(cap: int) -> RiskPolicyDefinition:
    """The compiled envelope with a clustering cap over a one-day window."""
    return RiskPolicyDefinition.model_validate(
        {
            **compiled_default_risk_policy().model_dump(mode="python"),
            "max_fleet_entries_per_window": cap,
            "fleet_entry_window_minutes": 1440,
        }
    )


@pytest.mark.anyio
async def test_second_book_on_a_crowded_bar_is_skipped_without_pausing() -> None:
    """Cap 1: the first book enters; the second is denied, rests nothing and keeps running."""
    store = InMemoryExecutionStore()
    strategy = _always_entry_strategy()
    first = await _running_snapshot(store, strategy)
    second = await _running_snapshot(store, strategy)
    policy = _clustered_policy(1)
    warmup = _candles(30, low_offset=Decimal("0.01"))

    entered = await process_closed_bar(
        first,
        strategy=strategy,
        product=_product(),
        candles=warmup,
        broker=PaperBroker(),
        store=store,
        risk_policy=policy,
        portfolio=(second,),
    )
    skipped = await process_closed_bar(
        second,
        strategy=strategy,
        product=_product(),
        candles=warmup,
        broker=PaperBroker(),
        store=store,
        risk_policy=policy,
        portfolio=(entered,),
    )

    assert [intent.purpose for intent in entered.intents] == [IntentPurpose.ENTRY]
    assert skipped.orders == ()
    assert skipped.intents == ()
    assert skipped.deployment.status is DeploymentStatus.RUNNING
    assert skipped.deployment.phase is RuntimePhase.FLAT
    assert skipped.deployment.last_signal == "matched"


@pytest.mark.anyio
async def test_reprice_of_a_working_entry_proceeds_under_a_binding_cap() -> None:
    """Cap 1 is reached by the book's own entry; its reprice still rests a replacement.

    A fresh entry would be denied here, so the replacement proves the reprice is admitted
    with ``readmits_working_entry``.
    """
    store = InMemoryExecutionStore()
    strategy = _always_entry_strategy(on_unfilled_entry="reprice")
    snapshot = await _running_snapshot(store, strategy)
    policy = _clustered_policy(1)
    warmup = _candles(30, low_offset=Decimal("0"))
    pending = await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=warmup,
        broker=PaperBroker(),
        store=store,
        risk_policy=policy,
    )
    original_id = pending.orders[0].id
    last = warmup[-1]
    wait_bars: list[Candle] = []
    current = pending
    for offset in (1, 2):
        bar = Candle(
            starts_at=last.starts_at + timedelta(hours=offset),
            open=last.close + Decimal("10"),
            high=last.close + Decimal("12"),
            low=last.close + Decimal("9"),
            close=last.close + Decimal("10"),
            volume=Decimal("10"),
        )
        wait_bars.append(bar)
        current = await process_closed_bar(
            current,
            strategy=strategy,
            product=_product(),
            candles=warmup + tuple(wait_bars),
            broker=PaperBroker(),
            store=store,
            risk_policy=policy,
        )

    open_entries = [order for order in current.orders if order.status is OrderStatus.OPEN]
    assert len(open_entries) == 1
    assert open_entries[0].id != original_id
    assert current.deployment.status is DeploymentStatus.RUNNING
    assert [intent.purpose for intent in current.intents] == [
        IntentPurpose.ENTRY,
        IntentPurpose.ENTRY,
    ]


@pytest.mark.anyio
async def test_discretionary_entry_is_gated_by_the_fleet_cap() -> None:
    """A strategy book's recent entry fills cap 1, so a discretionary ticket is refused."""
    store = InMemoryExecutionStore()
    strategy = _always_entry_strategy()
    book = await _running_snapshot(store, strategy)
    risk = InMemoryRiskPolicyStore()
    await risk.publish(_clustered_policy(1))
    entered = await process_closed_bar(
        book,
        strategy=strategy,
        product=_product(),
        candles=_candles(30, low_offset=Decimal("0.01")),
        broker=PaperBroker(),
        store=store,
        risk_policy=_clustered_policy(1),
    )
    assert len(entered.intents) == 1

    with pytest.raises(ExecutionConflictError, match="Fleet entry cluster limit: 1 new entries"):
        await place_discretionary_order(
            store=store,
            broker=PaperBroker(),
            market_data=MarketDataService(DemoMarketData()),
            request=_discretionary_request(),
            live_allowed=False,
            risk_store=risk,
        )
    discretionary = [
        deployment for deployment in store.deployments.values() if deployment.strategy_id is None
    ]
    assert all(deployment.status is not DeploymentStatus.PAUSED for deployment in discretionary)
