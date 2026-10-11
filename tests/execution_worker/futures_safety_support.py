"""Synthetic venue and real fenced worker helpers for safety regressions."""

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from tests.execution.decision_support import Catalog
from tests.execution.test_paper_futures_books import _PERP, _candles, _strategy
from thytrader.execution.paper import PaperBroker
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.submit import submit_intent
from thytrader.execution_worker.service import _process_one
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.trading.models import (
    DeploymentSnapshot,
    DeploymentStatus,
    IntentPurpose,
    LifecycleCommand,
    Order,
    OrderKind,
    OrderSide,
)

if TYPE_CHECKING:
    from uuid import UUID

    from tests.execution.futures_live_fixture import FakeVenue
    from thytrader.trading.store import ExecutionStore


async def step(store: ExecutionStore, venue: FakeVenue, identity: UUID) -> DeploymentSnapshot:
    """Run the actual leased, revision-fenced no-candle worker with entries disabled."""
    current = await store.get_deployment(identity)
    await _process_one(
        deployment_id=identity,
        store=store,
        publication_store=Catalog(
            _strategy().model_copy(update={"strategy_id": current.deployment.strategy_id})
            if current.deployment.strategy_id is not None
            else _strategy()
        ),
        market_data=MarketDataService(DemoMarketData()),
        paper_broker=PaperBroker(),
        live_broker=venue.broker(),
        quote_reader=None,
        risk_policy=compiled_default_risk_policy().model_copy(
            update={
                "futures": FuturesRiskPolicy(live_enabled=False, live_derisk_margin_ratio="1.5")
            }
        ),
        portfolio=(current,),
        user_feed_store=None,
        memory_store=None,
    )
    return await store.get_deployment(identity)


async def stop_book(
    store: ExecutionStore,
    snapshot: DeploymentSnapshot,
    command: LifecycleCommand = LifecycleCommand.MANAGED_SHUTDOWN,
) -> None:
    """Persist synthetic operator shutdown without changing its accounting."""
    await store.save_deployment(
        replace(
            snapshot.deployment,
            status=DeploymentStatus.STOPPED,
            lifecycle_command=command,
        )
    )


async def protect(
    store: ExecutionStore,
    venue: FakeVenue,
    snapshot: DeploymentSnapshot,
) -> tuple[DeploymentSnapshot, Order]:
    """Rest a real adapter-managed protective SELL order with durable intent identity."""
    order = await submit_intent(
        store=store,
        broker=venue.broker(),
        deployment_id=snapshot.deployment.id,
        product_id=_PERP,
        purpose=IntentPurpose.STOP,
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=Decimal("0.02"),
        price=Decimal(89),
        stop_trigger_price=Decimal(90),
        candle=_candles(31)[-1],
    )
    current = await reconcile_open_orders(
        await store.get_deployment(snapshot.deployment.id),
        broker=venue.broker(),
        store=store,
    )
    return current, order


def cancels(venue: FakeVenue) -> int:
    """Count actual transport cancellation requests, not mocked helper invocations."""
    return sum(path.endswith("/batch_cancel") for _, path, _ in venue.calls)


def closes(venue: FakeVenue) -> int:
    """Count actual transport reduction POSTs."""
    return sum(path.endswith("/close_position") for _, path, _ in venue.calls)
