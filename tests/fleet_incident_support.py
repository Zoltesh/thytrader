"""The 2026-10-10 fleet entry block, rebuilt record for record (ADR 0130 regression).

A legacy order of the stopped 5m BTC-USDC plumbing-test book was FILLED with
``filled_quantity`` ``0`` but had one applied fill of 0.00014174. The opening-accounting
replay rejected the book, ``reconstruct_day_open`` returned ``None``, and the daily-loss
breaker failed closed with ``BREAKER_MARK_MISSING`` for every live USDC entry while every
operator report looked healthy.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from thytrader.risk.models import RiskPolicyDefinition, compiled_default_risk_policy
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)

INCIDENT_AT = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
LEGACY_DEPLOYMENT_ID = UUID("01a0f90a-9834-7d0e-afad-ef0b538fee40")
LEGACY_ORDER_ID = UUID("01a0f93b-9591-7ee1-b037-eae7b079c1b6")
LEGACY_EXIT_ORDER_ID = UUID("01a0f93b-9591-7ee1-b037-eae7b079c1b7")
NEAR_DEPLOYMENT_ID = UUID("01a10000-0000-7000-8000-00000000ea01")
JTO_DEPLOYMENT_ID = UUID("01a10000-0000-7000-8000-00000000ea02")
QUANTITY = Decimal("0.00014174")
BUY_PRICE = Decimal("84660.06")
SELL_PRICE = Decimal("84599.54")
BUY_FEE = Decimal("0.06")
SELL_FEE = Decimal("0.11")
LEGACY_OPENED = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)
EXPECTED_GAP = f"order {LEGACY_ORDER_ID} FILLED with filled_quantity 0 but fills sum 0.00014174"


def incident_policy() -> RiskPolicyDefinition:
    """The live USDC envelope of policy v13 that mattered: 25 USDC daily loss."""
    return compiled_default_risk_policy().model_copy(
        update={"max_daily_loss_quote": "25", "daily_loss_limit_fraction": "0.2"}
    )


@dataclass(frozen=True, slots=True)
class Incident:
    """The legacy stopped book and two running live USDC books."""

    legacy: DeploymentSnapshot
    running: tuple[DeploymentSnapshot, ...]

    @property
    def snapshots(self) -> tuple[DeploymentSnapshot, ...]:
        """Every book the gate reads."""
        return (self.legacy, *self.running)


def incident(*, repaired: bool = False) -> Incident:
    """Build the books; ``repaired`` applies the guarded single-row fix of 2026-10-10."""
    cash_change = -QUANTITY * BUY_PRICE - BUY_FEE + QUANTITY * SELL_PRICE - SELL_FEE
    legacy = Deployment(
        id=LEGACY_DEPLOYMENT_ID,
        strategy_fingerprint="sha256:" + "b" * 64,
        strategy_id=UUID("01a0f900-0000-7000-8000-000000000001"),
        kind=DeploymentKind.STRATEGY,
        product_id="BTC-USDC",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.STOPPED,
        cash=Decimal("100") + cash_change,
        phase=RuntimePhase.FLAT,
        created_at=LEGACY_OPENED,
        updated_at=LEGACY_OPENED,
        timeframe="5m",
        initial_equity=Decimal("100"),
    )
    buy = Order(
        id=LEGACY_ORDER_ID,
        deployment_id=legacy.id,
        intent_id=UUID("01a0f93b-0000-7000-8000-000000000001"),
        client_order_id="plumbing-buy",
        product_id="BTC-USDC",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=QUANTITY,
        status=OrderStatus.FILLED,
        created_at=LEGACY_OPENED,
        updated_at=LEGACY_OPENED,
        price=BUY_PRICE,
        filled_quantity=QUANTITY if repaired else Decimal("0"),
    )
    sell = replace(
        buy,
        id=LEGACY_EXIT_ORDER_ID,
        intent_id=UUID("01a0f93b-0000-7000-8000-000000000002"),
        client_order_id="plumbing-sell",
        side=OrderSide.SELL,
        kind=OrderKind.MARKETABLE,
        price=SELL_PRICE,
        filled_quantity=QUANTITY,
    )
    fills = (
        _fill(legacy.id, buy.id, "plumbing-buy-fill", BUY_PRICE, BUY_FEE),
        _fill(legacy.id, sell.id, "plumbing-sell-fill", SELL_PRICE, SELL_FEE),
    )
    return Incident(
        legacy=DeploymentSnapshot(deployment=legacy, orders=(buy, sell), fills=fills),
        running=(
            DeploymentSnapshot(deployment=_running(NEAR_DEPLOYMENT_ID, "NEAR-USDC")),
            DeploymentSnapshot(deployment=_running(JTO_DEPLOYMENT_ID, "JTO-USDC")),
        ),
    )


async def seeded_store(*, repaired: bool = False) -> InMemoryExecutionStore:
    """An execution store holding the incident's books, orders and applied fills."""
    store = InMemoryExecutionStore()
    books = incident(repaired=repaired)
    for snapshot in books.snapshots:
        await store.create_deployment(snapshot.deployment)
        for order in snapshot.orders:
            await store.save_order(order)
        for fill in snapshot.fills:
            await store.save_fill(fill)
    return store


async def repair_legacy_order(store: InMemoryExecutionStore) -> None:
    """Apply the 2026-10-10 repair: the legacy order's filled quantity becomes its fills."""
    order = store.orders[LEGACY_ORDER_ID]
    await store.save_order(replace(order, filled_quantity=QUANTITY))


def _running(deployment_id: UUID, product_id: str) -> Deployment:
    """A running flat live USDC strategy book with an observed venue quote balance."""
    opened = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)
    return Deployment(
        id=deployment_id,
        strategy_fingerprint="sha256:" + "c" * 64,
        strategy_id=UUID(int=deployment_id.int + 1),
        kind=DeploymentKind.STRATEGY,
        product_id=product_id,
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("0"),
        phase=RuntimePhase.FLAT,
        created_at=opened,
        updated_at=INCIDENT_AT,
        timeframe="2h",
        venue_available_quote=Decimal("300"),
    )


def _fill(deployment_id: UUID, order_id: UUID, venue_id: str, price: Decimal, fee: Decimal) -> Fill:
    """One applied fill of the plumbing round trip."""
    return Fill(
        id=UUID(int=order_id.int + 100),
        deployment_id=deployment_id,
        order_id=order_id,
        venue_fill_id=venue_id,
        price=price,
        quantity=QUANTITY,
        fee=fee,
        filled_at=LEGACY_OPENED,
        economics_applied_at=LEGACY_OPENED,
    )
