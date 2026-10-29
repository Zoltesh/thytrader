"""Apply one reconcile pass's new venue fills in execution order across a book's orders.

Reconcile reads each watched order in turn and used to apply that order's unseen fills as it
went, so economics followed the walk, not the market: after an outage a pyramid add that
filled before the entry's take-profit could be applied after it, and a child's exit before
its parent's entry. Here every watched order's fills are listed first and the unseen ones
are applied together by fill time, a parent's before its child's on a tie, whenever more than
one order has new fills (one order's fills already apply in its own order). The walk then
reads each order as before, including one whose fills were applied here; the listings are
reused, so the venue is asked once.

A parent whose fills cannot be listed, or whose listing covers less than the venue reports
executed or less than its attached children already sold, is short: its children's fills are
held for this pass (their listing reads as failed), so a child's exit is never projected
against an entry whose economics are unknown. The walk re-lists a short parent when its read
reports more executed than the listing shows, which can end the hold within the pass.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.broker import BrokerError, ClientOrderLookup
from thytrader.execution.paper import PaperBroker
from thytrader.trading.fill_ledger import applied_fill_quantity, creation_order, ingest_fill
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.models import Fill, OrderKind, OrderSide
from thytrader.trading.overlay import InstrumentScopedStore, overlay_snapshot

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from uuid import UUID

    from thytrader.execution.broker import Broker, SubmitResult
    from thytrader.market_data.models import Candle
    from thytrader.trading.models import DeploymentSnapshot, Order
    from thytrader.trading.store import ExecutionStore

_ZERO = Decimal(0)
PARENT_FILLS_UNREAD = "Attached child fills are held until the parent's fills are read."


class _ListedFills:
    """Broker that answers this pass's fill listings from the ones already read."""

    def __init__(self, inner: Broker, *, plan: _Plan) -> None:
        self._inner = inner
        self._plan = plan

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
        """Delegate."""
        return await self._inner.place_order(
            client_order_id=client_order_id,
            product_id=product_id,
            side=side,
            kind=kind,
            quantity=quantity,
            price=price,
            stop_trigger_price=stop_trigger_price,
            take_profit_price=take_profit_price,
        )

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Delegate."""
        return await self._inner.cancel_order(
            venue_order_id=venue_order_id, client_order_id=client_order_id
        )

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Delegate; a read reporting more executed than the listing shows re-lists it."""
        result = await self._inner.get_order(
            venue_order_id=venue_order_id, client_order_id=client_order_id
        )
        plan = self._plan
        if result.attached_child_venue_order_id:
            plan.parent_of[result.attached_child_venue_order_id] = venue_order_id
        plan.reported[venue_order_id] = max(
            plan.reported.get(venue_order_id, _ZERO), result.filled_quantity
        )
        listing = plan.listed.get(venue_order_id)
        product = plan.products.get(venue_order_id)
        if listing is not None and product is not None and _total(listing) < result.filled_quantity:
            try:
                plan.record(
                    venue_order_id,
                    await self._inner.list_fills(product_id=product, order_id=venue_order_id),
                )
            except BrokerError:
                plan.listed.pop(venue_order_id, None)
                plan.short.add(venue_order_id)
        return result

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Reuse this pass's listing; a child of a short parent reads as a failed listing."""
        plan = self._plan
        if order_id is None:
            return await self._inner.list_fills(product_id=product_id, order_id=order_id)
        if plan.parent_of.get(order_id) in plan.short:
            raise BrokerError(PARENT_FILLS_UNREAD)
        if order_id in plan.listed:
            return plan.listed[order_id]
        fills = await self._inner.list_fills(product_id=product_id, order_id=order_id)
        plan.products[order_id] = product_id
        plan.record(order_id, fills)
        return fills

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Delegate."""
        return self._inner.match_open_order(order, candle)

    def maker_limit_price(
        self, *, product_id: str, mark: Decimal, side: OrderSide = OrderSide.BUY
    ) -> Decimal:
        """Delegate."""
        return self._inner.maker_limit_price(product_id=product_id, mark=mark, side=side)


class _ListedFillsWithLookup(_ListedFills):
    """Keeps the optional client-order lookup of a broker that has it."""

    async def find_order_by_client_id(
        self, *, client_order_id: str, product_id: str, submitted_at: datetime
    ) -> SubmitResult | None:
        """Delegate."""
        inner = self._inner
        if not isinstance(inner, ClientOrderLookup):
            raise TypeError("client-order lookup capability is unavailable")
        return await inner.find_order_by_client_id(
            client_order_id=client_order_id, product_id=product_id, submitted_at=submitted_at
        )


async def apply_new_fills_in_execution_order(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
    product_id: str | None,
    cooldown_bars: int,
    watched: Callable[[Order], bool],
) -> tuple[DeploymentSnapshot, Broker, frozenset[UUID]]:
    """Apply every watched order's unseen venue fills by fill time.

    Returns the refreshed book, the broker the walk should read through, and the orders
    whose fills were applied here. A paper broker keeps its fills locally, so it is
    returned unchanged.
    """
    if isinstance(broker, PaperBroker):
        return snapshot, broker, frozenset()
    plan = await _list_watched(snapshot, broker=broker, product_id=product_id, watched=watched)
    _mark_short_parents(plan, snapshot)
    new = _new_fills(plan, snapshot)
    applied = frozenset(order.id for _fill, order in new)
    if len(applied) < 2:
        new, applied = [], frozenset()
    current = snapshot
    for remote, order in sorted(new, key=_execution_order):
        latest = next((item for item in current.orders if item.id == order.id), order)
        product = _product(latest, current, product_id)
        await ingest_fill(
            overlay_snapshot(current, product),
            fill=Fill(
                id=uuid7(utc_now()),
                deployment_id=latest.deployment_id,
                order_id=latest.id,
                venue_fill_id=remote.venue_fill_id,
                price=remote.price,
                quantity=remote.quantity,
                fee=remote.fee,
                filled_at=remote.filled_at,
                venue_order_id=remote.venue_order_id,
            ),
            order=latest,
            store=InstrumentScopedStore(store, product),
            cooldown_bars=cooldown_bars,
        )
        current = await store.get_deployment(snapshot.deployment.id)
    kind = _ListedFillsWithLookup if isinstance(broker, ClientOrderLookup) else _ListedFills
    return current, kind(broker, plan=plan), applied


async def _list_watched(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    product_id: str | None,
    watched: Callable[[Order], bool],
) -> _Plan:
    """List every watched order's fills once, skipping children of an unreadable parent."""
    plan = _Plan()
    by_id = {order.id: order for order in snapshot.orders}
    for order in snapshot.orders:
        parent = by_id.get(order.parent_order_id) if order.parent_order_id else None
        if order.venue_order_id and parent is not None and parent.venue_order_id:
            plan.parent_of[order.venue_order_id] = parent.venue_order_id
        if order.venue_order_id:
            plan.reported[order.venue_order_id] = order.filled_quantity
    for order in sorted(snapshot.orders, key=creation_order):
        venue_order_id = order.venue_order_id
        if order.kind is OrderKind.ADOPTION or not venue_order_id or not watched(order):
            continue
        if plan.parent_of.get(venue_order_id) in plan.short:
            continue
        product = _product(order, snapshot, product_id)
        plan.products[venue_order_id] = product
        try:
            fills = await broker.list_fills(product_id=product, order_id=venue_order_id)
        except BrokerError:
            # The walk reads this order again and records the failure.
            plan.short.add(venue_order_id)
            continue
        plan.record(venue_order_id, fills)
    return plan


def _mark_short_parents(plan: _Plan, snapshot: DeploymentSnapshot) -> None:
    """A parent must have executed at least what its children sold before they project."""
    for child_venue, parent_venue in plan.parent_of.items():
        parent = next(
            (item for item in snapshot.orders if item.venue_order_id == parent_venue), None
        )
        if parent_venue in plan.listed:
            parent_total = _total(plan.listed[parent_venue])
        elif parent is not None:
            parent_total = applied_fill_quantity(snapshot, parent.id)
        else:
            parent_total = _ZERO
        child_total = _total(plan.listed.get(child_venue, ()))
        plan.required[parent_venue] = max(plan.required.get(parent_venue, _ZERO), child_total)
        if child_total > parent_total:
            plan.short.add(parent_venue)


def _new_fills(plan: _Plan, snapshot: DeploymentSnapshot) -> list[tuple[Fill, Order]]:
    """Unseen listed fills, without those of a short parent's children."""
    known = {fill.venue_fill_id for fill in snapshot.fills}
    new: list[tuple[Fill, Order]] = []
    for order in snapshot.orders:
        venue_order_id = order.venue_order_id or ""
        if plan.parent_of.get(venue_order_id) in plan.short:
            continue
        for fill in plan.listed.get(venue_order_id, ()):
            if fill.venue_fill_id not in known:
                known.add(fill.venue_fill_id)
                new.append((fill, order))
    return new


class _Plan:
    """One pass's listings, parent links and the parents whose fills are short."""

    def __init__(self) -> None:
        self.listed: dict[str, tuple[Fill, ...]] = {}
        self.products: dict[str, str] = {}
        self.parent_of: dict[str, str] = {}
        self.reported: dict[str, Decimal] = {}
        self.required: dict[str, Decimal] = {}
        self.short: set[str] = set()

    def record(self, venue_order_id: str, fills: tuple[Fill, ...]) -> None:
        """Keep a listing; a parent stays short until it covers its reads and its children."""
        self.listed[venue_order_id] = fills
        total = _total(fills)
        if total >= self.reported.get(venue_order_id, _ZERO) and total >= self.required.get(
            venue_order_id, _ZERO
        ):
            self.short.discard(venue_order_id)
        elif venue_order_id in self.required:
            self.short.add(venue_order_id)


def _total(fills: tuple[Fill, ...]) -> Decimal:
    return sum((fill.quantity for fill in fills), _ZERO)


def _execution_order(item: tuple[Fill, Order]) -> tuple[datetime, bool, str]:
    """Fill time, a parent's fill before its child's on a tie, then the venue fill id."""
    fill, order = item
    return fill.filled_at, order.parent_order_id is not None, fill.venue_fill_id


def _product(order: Order, snapshot: DeploymentSnapshot, product_id: str | None) -> str:
    return order.product_id or product_id or snapshot.deployment.product_id
