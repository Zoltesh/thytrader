"""Apply one reconcile pass's new venue fills in execution order across a book's orders.

Reconcile reads each watched order in turn and used to apply that order's unseen fills as it
went, so economics followed the walk, not the market: after an outage a pyramid add that
filled before the entry's take-profit could be applied after it, and a child's exit before
its parent's entry. Here every watched order's fills are listed first and the unseen ones
are applied together by fill time, a parent's before its child's on a tie, whenever more than
one order has new fills (one order's fills already apply in its own order). The walk then
reads each order as before, including one whose fills were applied here; the listings are
reused, so the venue is asked once.

When a parent's fills cannot be listed, its attached children's fills are held back for
this pass (their listing reads as failed), so a child's exit is never projected against an
entry whose economics are unknown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.broker import BrokerError, ClientOrderLookup
from thytrader.execution.paper import PaperBroker
from thytrader.trading.fill_ledger import creation_order, ingest_fill
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.models import Fill, OrderKind, OrderSide
from thytrader.trading.overlay import InstrumentScopedStore, overlay_snapshot

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID

    from thytrader.execution.broker import Broker, SubmitResult
    from thytrader.market_data.models import Candle
    from thytrader.trading.models import DeploymentSnapshot, Order
    from thytrader.trading.store import ExecutionStore

PARENT_FILLS_UNREAD = "Attached child fills are held until the parent's fills are read."


class _ListedFills:
    """Broker that answers this pass's fill listings from the ones already read."""

    def __init__(
        self, inner: Broker, *, listed: dict[str, tuple[Fill, ...]], held: frozenset[str]
    ) -> None:
        self._inner = inner
        self._listed = listed
        self._held = held

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
        """Delegate."""
        return await self._inner.get_order(
            venue_order_id=venue_order_id, client_order_id=client_order_id
        )

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Reuse this pass's listing; a held child reads as a failed listing."""
        if order_id is not None and order_id in self._held:
            raise BrokerError(PARENT_FILLS_UNREAD)
        if order_id is not None and order_id in self._listed:
            return self._listed[order_id]
        return await self._inner.list_fills(product_id=product_id, order_id=order_id)

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
    listed: dict[str, tuple[Fill, ...]] = {}
    unread: set[UUID] = set()
    held: set[str] = set()
    known = {fill.venue_fill_id for fill in snapshot.fills}
    new: list[tuple[Fill, Order]] = []
    for order in sorted(snapshot.orders, key=creation_order):
        venue_order_id = order.venue_order_id
        if order.kind is OrderKind.ADOPTION or not venue_order_id or not watched(order):
            continue
        if order.parent_order_id is not None and order.parent_order_id in unread:
            unread.add(order.id)
            held.add(venue_order_id)
            continue
        try:
            fills = await broker.list_fills(
                product_id=_product(order, snapshot, product_id), order_id=venue_order_id
            )
        except BrokerError:
            # The walk reads this order again and records the failure.
            unread.add(order.id)
            continue
        listed[venue_order_id] = fills
        for fill in fills:
            if fill.venue_fill_id not in known:
                known.add(fill.venue_fill_id)
                new.append((fill, order))
    current = snapshot
    applied = frozenset(order.id for _fill, order in new)
    if len(applied) < 2:
        new, applied = [], frozenset()
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
    return current, kind(broker, listed=listed, held=frozenset(held)), applied


def _execution_order(item: tuple[Fill, Order]) -> tuple[datetime, bool, str]:
    """Fill time, a parent's fill before its child's on a tie, then the venue fill id."""
    fill, order = item
    return fill.filled_at, order.parent_order_id is not None, fill.venue_fill_id


def _product(order: Order, snapshot: DeploymentSnapshot, product_id: str | None) -> str:
    return order.product_id or product_id or snapshot.deployment.product_id
