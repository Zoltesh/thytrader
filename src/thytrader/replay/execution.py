"""Isolated execution replay using the shipped engine and in-memory accounting store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.broker import BrokerError, SubmitResult
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.replay.broker import RecordingBroker
from thytrader.replay.lookup import replay_broker
from thytrader.replay.steps import ExecutionOutcome, ReplayStep
from thytrader.runtime_inputs import InputTape, runtime_input_scope
from thytrader.trading.entry_latch import entry_inhibition_scope
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import Fill, Order, OrderKind, OrderSide, snapshot_positions

__all__ = ["OfflineBroker", "RecordingBroker", "execute", "outcome", "seed_store"]

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.models import DeploymentSnapshot


class OfflineBroker:
    """No network construction or real orders; unscripted broker access is an evidence error."""

    async def place_order(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        kind: OrderKind,
        quantity: Decimal,
        price: Decimal | None,
        stop_trigger_price: Decimal | None = None,
        take_profit_price: Decimal | None = None,
    ) -> SubmitResult:
        """Refuse an unrecorded submit, never falling back to a real adapter."""
        del client_order_id, product_id, side, kind, quantity, price
        del stop_trigger_price, take_profit_price
        raise BrokerError("Unscripted offline broker input")

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Refuse unrecorded cancellations."""
        del venue_order_id, client_order_id
        raise BrokerError("Unscripted offline broker input")

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Refuse unrecorded observations."""
        del venue_order_id, client_order_id
        raise BrokerError("Unscripted offline broker input")

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Refuse unrecorded fill responses."""
        del product_id, order_id
        raise BrokerError("Unscripted offline broker input")

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """No invented candle-touch fills on an execution replay."""
        del order, candle
        raise ValueError("Unscripted offline broker input")

    def maker_limit_price(
        self, *, product_id: str, mark: Decimal, side: OrderSide = OrderSide.BUY
    ) -> Decimal:
        """No invented current quotes on an execution replay."""
        del product_id, mark, side
        raise ValueError("Unscripted offline broker input")


async def seed_store(snapshot: DeploymentSnapshot) -> InMemoryExecutionStore:
    """Restore exact observed input through public store methods, not private table access."""
    store = InMemoryExecutionStore()
    await store.create_deployment(snapshot.deployment)
    for intent in snapshot.intents:
        await store.save_intent(intent)
    for order in snapshot.orders:
        await store.restore_order(order)
    for fill in snapshot.fills:
        await store.save_fill(fill)
    for position in snapshot_positions(snapshot):
        await store.save_position(position, deployment_id=snapshot.deployment.id)
    return store


def outcome(snapshot: DeploymentSnapshot) -> ExecutionOutcome:
    """Project complete economic and order/fill boundaries without altering evidence."""
    return ExecutionOutcome(
        cash=snapshot.deployment.cash,
        deployment_status=snapshot.deployment.status,
        daily_loss_latched=snapshot.deployment.daily_loss_latched,
        drawdown_latched=snapshot.deployment.drawdown_latched,
        phase=snapshot.deployment.phase,
        positions=snapshot_positions(snapshot),
        orders=snapshot.orders,
        fills=snapshot.fills,
    )


async def execute(step: ReplayStep, strategy: StrategyDefinition) -> ExecutionOutcome:
    """Run the actual closed-bar engine on captured scripted inputs in an isolated store."""
    store = await seed_store(step.before)
    tape = InputTape(replay=step.runtime_inputs)
    broker = replay_broker(
        step.broker_events, supports_client_lookup=step.broker_supports_client_lookup
    )
    with (
        runtime_input_scope(tape),
        entry_inhibition_scope(step.before.deployment.mode, step.entry_inhibited),
    ):
        if step.operation == "reconcile":
            snapshot = await reconcile_open_orders(
                step.before, broker=broker, store=store, cooldown_bars=0
            )
        else:
            snapshot = await process_closed_bar(
                step.before,
                strategy=strategy,
                product=step.product,
                candles=step.candles,
                broker=broker,
                store=store,
                risk_policy=step.risk_policy,
                allow_new_entries=step.allow_new_entries,
                portfolio=step.portfolio,
                htf_candles=step.htf_candles,
                indicator_timeframe_candles={w.key: w.candles for w in step.indicator_windows},
                reference_candles={w.key: w.candles for w in step.reference_windows},
                marks=dict(step.marks),
                live_base_available=step.live_base_available,
                fee_profile=step.fee_profile,
            )
    if not tape.complete or not broker.complete:
        raise ValueError("Runtime tape was not completely consumed")
    return outcome(snapshot)
