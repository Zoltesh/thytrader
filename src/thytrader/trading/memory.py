"""In-memory execution store for tests and database-free API doubles."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from thytrader.trading.fill_ledger import (
    applied_fill_quantity,
    fill_projection_deployment,
    project_fill_economics,
)
from thytrader.trading.ids import utc_now
from thytrader.trading.ledger import (
    MAX_POSITION_FEE_FILLS,
    LedgerFill,
    remaining_position_entry_fees,
)
from thytrader.trading.models import (
    Deployment,
    DeploymentBookTotals,
    DeploymentSnapshot,
    DeploymentStatus,
    DeploymentSummarySnapshot,
    ExecutionConflictError,
    ExecutionStoreError,
    Fill,
    InstrumentRuntime,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderStatus,
    PaginatedFills,
    PaginatedOrders,
    Position,
    mirrors_primary_runtime,
    resolved_product_id,
    runtime_from_deployment,
)
from thytrader.trading.pagination import (
    decode_cursor,
    decode_order_cursor,
    encode_cursor,
    encode_order_cursor,
)
from thytrader.trading.protection import working_order_count
from thytrader.trading.twins import DeploymentTwinLink, TwinConflictError, comparable_twins

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from decimal import Decimal

    from thytrader.strategies.snapshots import StrategySnapshot
    from thytrader.trading.entry_latch import EntryGate


def _position_key(deployment_id: UUID, product_id: str) -> tuple[UUID, str]:
    """Return the in-memory key for one product book."""
    return (deployment_id, product_id)


class InMemoryExecutionStore:
    """Retain execution records in process memory."""

    def __init__(self) -> None:
        """Start with no deployments."""
        self.deployments: dict[UUID, Deployment] = {}
        self.twin_links: dict[UUID, DeploymentTwinLink] = {}
        self.intents: dict[UUID, OrderIntent] = {}
        self.orders: dict[UUID, Order] = {}
        self.fills: dict[UUID, Fill] = {}
        self.positions: dict[tuple[UUID, str], Position] = {}
        self.instrument_runtimes: dict[tuple[UUID, str], InstrumentRuntime] = {}
        self._fill_keys: set[tuple[UUID, str]] = set()
        self._applied_fill_keys: set[tuple[UUID, str]] = set()
        # Database-free test backend starts with an explicitly clear memory latch.
        self._entry_gate: EntryGate | None = None

    def bind_entry_gate(self, gate: EntryGate) -> None:
        """Bind the fleet latch that must be held across a start insert."""
        self._entry_gate = gate

    async def get_twin_link(self, deployment_id: UUID) -> DeploymentTwinLink | None:
        """Read a saved pair from either member."""
        return next(
            (
                link
                for link in self.twin_links.values()
                if deployment_id in (link.paper_deployment_id, link.live_deployment_id)
            ),
            None,
        )

    async def list_twin_links(self) -> tuple[DeploymentTwinLink, ...]:
        """Return explicit pairs in deterministic newest-linked order."""
        return tuple(
            sorted(
                self.twin_links.values(),
                key=lambda link: (link.linked_at, link.paper_deployment_id),
                reverse=True,
            )
        )

    async def link_twins(
        self,
        deployment_id: UUID,
        counterpart_id: UUID,
        *,
        snapshots: tuple[StrategySnapshot, StrategySnapshot] | None = None,
    ) -> DeploymentTwinLink:
        """Validate and link without yielding, so competing mutations are atomic."""
        first = self.deployments.get(deployment_id)
        second = self.deployments.get(counterpart_id)
        if first is None or second is None:
            raise ExecutionStoreError("Deployment was not found.")
        paper, live = comparable_twins(first, second, snapshots=snapshots)
        for current in self.twin_links.values():
            if current.paper_deployment_id == paper.id and current.live_deployment_id == live.id:
                return current
            if current.paper_deployment_id == paper.id or current.live_deployment_id == live.id:
                raise TwinConflictError(
                    "A bot already has a twin. Unlink its current partner first."
                )
        link = DeploymentTwinLink(paper.id, live.id, utc_now())
        self.twin_links[paper.id] = link
        return link

    async def unlink_twins(self, deployment_id: UUID, counterpart_id: UUID) -> None:
        """Remove the expected partner atomically; protect a replacement from stale requests."""
        if deployment_id not in self.deployments or counterpart_id not in self.deployments:
            raise ExecutionStoreError("Deployment was not found.")
        current = next(
            (
                link
                for link in self.twin_links.values()
                if deployment_id in (link.paper_deployment_id, link.live_deployment_id)
            ),
            None,
        )
        if current is None:
            return
        if current.counterpart(deployment_id) != counterpart_id:
            raise TwinConflictError(
                "The twin partner changed. Read the current link before unlinking."
            )
        del self.twin_links[current.paper_deployment_id]

    async def create_deployment(self, deployment: Deployment) -> Deployment:
        """Insert one new deployment row unless the bound latch refuses the mode."""
        gate = self._entry_gate
        if gate is None:
            self.deployments[deployment.id] = deployment
            return deployment
        async with gate.hold():
            gate.raise_if_inhibited(deployment.mode.value, action="start")
            self.deployments[deployment.id] = deployment
        return deployment

    async def get_deployment(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Load one deployment with its related records or fail."""
        deployment = self.deployments.get(deployment_id)
        if deployment is None:
            raise ExecutionStoreError("Deployment was not found.")
        positions = tuple(
            position
            for (stored_id, _product), position in self.positions.items()
            if stored_id == deployment_id
        )
        runtimes = tuple(
            runtime
            for (stored_id, _product), runtime in self.instrument_runtimes.items()
            if stored_id == deployment_id
        )
        focused = _focused_position(positions, deployment.product_id)
        return DeploymentSnapshot(
            deployment=deployment,
            position=focused,
            orders=tuple(
                order for order in self.orders.values() if order.deployment_id == deployment_id
            ),
            fills=tuple(
                fill for fill in self.fills.values() if fill.deployment_id == deployment_id
            ),
            intents=tuple(
                intent for intent in self.intents.values() if intent.deployment_id == deployment_id
            ),
            positions=positions,
            instrument_runtimes=runtimes,
        )

    async def get_accounting_snapshot(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Read all products' current economics from the authoritative in-memory store."""
        return await self.get_deployment(deployment_id)

    async def get_deployment_summary(self, deployment_id: UUID) -> DeploymentSummarySnapshot:
        """Load positions and overlays without historical orders or fills."""
        snapshot = await self.get_deployment(deployment_id)
        return DeploymentSummarySnapshot(
            deployment=snapshot.deployment,
            position=snapshot.position,
            positions=snapshot.positions,
            instrument_runtimes=snapshot.instrument_runtimes,
            book_totals=DeploymentBookTotals(
                open_books=len(snapshot.positions),
                working_orders=working_order_count(snapshot.orders),
                fill_count=len(snapshot.fills),
            ),
            open_orders=tuple(
                order
                for order in snapshot.orders
                if order.status in {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}
            ),
        )

    async def list_deployments(
        self, *, limit: int | None = None, offset: int = 0
    ) -> tuple[Deployment, ...]:
        """Return deployments newest-updated first, optionally paginated."""
        rows = sorted(self.deployments.values(), key=lambda item: item.updated_at, reverse=True)
        if offset:
            rows = rows[offset:]
        if limit is not None:
            rows = rows[:limit]
        return tuple(rows)

    async def get_position_entry_fees(
        self, position: Position, *, product_id: str
    ) -> Decimal | None:
        """Replay at most 1,000 applied fills of the current product book for its fees."""
        deployment = self.deployments.get(position.deployment_id)
        if deployment is None:
            raise ExecutionStoreError("Deployment was not found.")
        selected = sorted(
            (
                fill
                for fill in self.fills.values()
                if fill.deployment_id == position.deployment_id
                and fill.economics_applied_at is not None
                and fill.filled_at >= position.entered_bar
                and (order := self.orders.get(fill.order_id)) is not None
                and order.deployment_id == position.deployment_id
                and resolved_product_id(order.product_id, deployment) == product_id
            ),
            key=lambda fill: (fill.filled_at, fill.venue_fill_id),
        )[: MAX_POSITION_FEE_FILLS + 1]
        fills = tuple(
            LedgerFill(
                side=self.orders[fill.order_id].side,
                price=fill.price,
                quantity=fill.quantity,
                fee=fill.fee,
                filled_at=fill.filled_at,
            )
            for fill in selected
        )
        return remaining_position_entry_fees(position, fills)

    async def list_fills(
        self, deployment_id: UUID, *, limit: int, cursor: str | None = None
    ) -> PaginatedFills:
        """Return one descending page of fills for one deployment."""
        if limit < 1:
            raise ExecutionStoreError("Fill page limit must be positive.")
        fills = [fill for fill in self.fills.values() if fill.deployment_id == deployment_id]
        fills.sort(key=lambda item: (item.filled_at, str(item.id)), reverse=True)
        if cursor is not None:
            try:
                filled_at, row_id = decode_cursor(cursor)
            except ValueError as error:
                raise ExecutionStoreError("Invalid pagination cursor.") from error
            fills = [fill for fill in fills if (fill.filled_at, fill.id) < (filled_at, row_id)]
        page = tuple(fills[:limit])
        deployment = self.deployments.get(deployment_id)
        if deployment is None:
            raise ExecutionStoreError("Deployment was not found.")
        order_products = tuple(
            (
                fill.order_id,
                resolved_product_id(self.orders[fill.order_id].product_id, deployment),
            )
            for fill in page
            if fill.order_id in self.orders
        )
        next_cursor = None
        if len(fills) > limit:
            last = fills[limit - 1]
            next_cursor = encode_cursor(filled_at=last.filled_at, row_id=last.id)
        return PaginatedFills(fills=page, next_cursor=next_cursor, order_products=order_products)

    async def list_orders(
        self, deployment_id: UUID, *, limit: int, cursor: str | None = None
    ) -> PaginatedOrders:
        """Return one descending page of orders for one deployment."""
        if limit < 1:
            raise ExecutionStoreError("Order page limit must be positive.")
        orders = [order for order in self.orders.values() if order.deployment_id == deployment_id]
        orders.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
        if cursor is not None:
            try:
                created_at, row_id = decode_order_cursor(cursor)
            except ValueError as error:
                raise ExecutionStoreError("Invalid pagination cursor.") from error
            orders = [
                order for order in orders if (order.created_at, order.id) < (created_at, row_id)
            ]
        page = tuple(orders[:limit])
        next_cursor = None
        if len(orders) > limit:
            last = orders[limit - 1]
            next_cursor = encode_order_cursor(created_at=last.created_at, row_id=last.id)
        return PaginatedOrders(orders=page, next_cursor=next_cursor)

    async def list_by_strategy(self, strategy_id: str) -> tuple[Deployment, ...]:
        """Return deployments for one strategy identity, newest-updated first."""
        matching = [
            item
            for item in self.deployments.values()
            if item.strategy_id is not None and str(item.strategy_id) == strategy_id
        ]
        return tuple(sorted(matching, key=lambda item: item.updated_at, reverse=True))

    async def list_by_strategy_ids(
        self, strategy_ids: Sequence[str]
    ) -> dict[str, tuple[Deployment, ...]]:
        """Return every deployment grouped per requested strategy identity."""
        buckets: dict[str, list[Deployment]] = {identity: [] for identity in strategy_ids}
        for item in self.deployments.values():
            if item.strategy_id is None:
                continue
            identity = str(item.strategy_id)
            bucket = buckets.get(identity)
            if bucket is not None:
                bucket.append(item)
        return {
            identity: tuple(sorted(bucket, key=lambda item: item.updated_at, reverse=True))
            for identity, bucket in buckets.items()
        }

    async def save_deployment(
        self,
        deployment: Deployment,
        *,
        expected_revision: int | None = None,
        instrument_runtime: InstrumentRuntime | None = None,
    ) -> Deployment:
        """Check the revision before mutating parent/runtime, without yielding between writes."""
        if instrument_runtime is not None and expected_revision is None:
            raise ExecutionStoreError("Atomic runtime saves require an expected revision.")
        current = self.deployments.get(deployment.id)
        if current is None:
            raise ExecutionStoreError("Deployment was not found.")
        if expected_revision is not None and current.revision != expected_revision:
            raise ExecutionConflictError("Deployment revision conflict.")
        saved = replace(deployment, revision=current.revision + 1)
        self.deployments[deployment.id] = saved
        if instrument_runtime is not None:
            self.instrument_runtimes[
                _position_key(deployment.id, instrument_runtime.product_id)
            ] = instrument_runtime
            return saved
        own = _position_key(deployment.id, deployment.product_id)
        rows = [
            runtime for key, runtime in self.instrument_runtimes.items() if key[0] == deployment.id
        ]
        if own in self.instrument_runtimes and mirrors_primary_runtime(saved, rows):
            self.instrument_runtimes[own] = runtime_from_deployment(saved, deployment.product_id)
        return saved

    async def save_breaker_pause(
        self,
        deployment_id: UUID,
        *,
        expected_revision: int,
        detail: str,
        daily_loss_latched: bool = False,
    ) -> Deployment:
        """Atomically change only breaker status/detail/latch, preserving independent state."""
        current = self.deployments.get(deployment_id)
        if current is None:
            raise ExecutionStoreError("Deployment was not found.")
        if current.revision != expected_revision:
            raise ExecutionConflictError("Deployment revision conflict.")
        running = current.status is DeploymentStatus.RUNNING
        saved = replace(
            current,
            status=DeploymentStatus.PAUSED if running else current.status,
            mismatch_detail=detail if running else current.mismatch_detail,
            daily_loss_latched=current.daily_loss_latched or daily_loss_latched,
            updated_at=utc_now(),
            revision=current.revision + 1,
        )
        self.deployments[deployment_id] = saved
        return saved

    async def acquire_worker_lease(
        self,
        deployment_id: UUID,
        *,
        holder: str,
        now: datetime,
        ttl: timedelta,
    ) -> Deployment | None:
        """Acquire or renew a fenced worker lease in process memory."""
        current = self.deployments.get(deployment_id)
        if current is None:
            raise ExecutionStoreError("Deployment was not found.")
        expires_at = current.worker_lease_expires_at
        holder_ok = (
            current.worker_lease_holder is None
            or expires_at is None
            or expires_at <= now
            or current.worker_lease_holder == holder
        )
        if not holder_ok:
            return None
        saved = replace(
            current,
            worker_lease_holder=holder,
            worker_lease_expires_at=now + ttl,
            revision=current.revision + 1,
        )
        self.deployments[deployment_id] = saved
        return saved

    async def save_intent(self, intent: OrderIntent) -> OrderIntent:
        """Insert one order intent before venue submission.

        Entry intents consult the bound fleet latch. Exit and protection intents
        do not, so disarm cannot block a risk-reducing order.
        """
        if intent.idempotency_key is not None:
            existing = await self.get_intent_by_idempotency_key(intent.idempotency_key)
            if existing is not None:
                raise ExecutionConflictError("idempotency_key already used")
        gate = self._entry_gate
        if intent.purpose is IntentPurpose.ENTRY and gate is not None:
            deployment = self.deployments.get(intent.deployment_id)
            if deployment is None:
                raise ExecutionStoreError("Entry deployment was not found.")
            async with gate.hold():
                gate.raise_if_inhibited(deployment.mode.value, action="entry")
                self.intents[intent.id] = intent
        else:
            self.intents[intent.id] = intent
        return intent

    async def read_entry_inhibition(self) -> dict[str, bool]:
        """Return the bound latch, or both modes clear when no latch is bound."""
        gate = self._entry_gate
        if gate is None:
            return {"paper": False, "live": False}
        snapshot = await gate.read_inhibition()
        return {"paper": snapshot.paper_inhibited, "live": snapshot.live_inhibited}

    async def save_order(self, order: Order) -> Order:
        """Insert or replace one venue-visible order snapshot."""
        existing = next(
            (
                item
                for item in self.orders.values()
                if item.client_order_id == order.client_order_id
            ),
            None,
        )
        if existing is not None:
            self.orders.pop(existing.id, None)
        self.orders[order.id] = order
        return order

    async def save_fill(self, fill: Fill) -> Fill:
        """Insert one fill, ignoring exact venue-fill duplicates."""
        key = (fill.deployment_id, fill.venue_fill_id)
        if key in self._fill_keys:
            return next(
                (
                    item
                    for item in self.fills.values()
                    if item.deployment_id == fill.deployment_id
                    and item.venue_fill_id == fill.venue_fill_id
                ),
                fill,
            )
        self._fill_keys.add(key)
        self.fills[fill.id] = fill
        if fill.economics_applied_at is not None:
            self._applied_fill_keys.add(key)
        return fill

    async def apply_fill_transaction(
        self,
        deployment_id: UUID,
        *,
        fill: Fill,
        order: Order,
        cooldown_bars: int = 0,
        timeframe: str | None = None,
    ) -> tuple[bool, DeploymentSnapshot]:
        """Insert fill evidence and apply economics in one in-memory step."""
        snapshot = await self.get_deployment(deployment_id)
        key = (fill.deployment_id, fill.venue_fill_id)
        existing = next(
            (
                item
                for item in snapshot.fills
                if item.deployment_id == fill.deployment_id
                and item.venue_fill_id == fill.venue_fill_id
            ),
            None,
        )
        if existing is not None and existing.economics_applied_at is not None:
            return False, snapshot
        if key not in self._fill_keys:
            self._fill_keys.add(key)
            self.fills[fill.id] = fill
        projected, stamped = project_fill_economics(
            snapshot,
            fill=existing or fill,
            order=order,
            cooldown_bars=cooldown_bars,
            timeframe=timeframe,
        )
        self.fills[stamped.id] = stamped
        self._applied_fill_keys.add(key)
        applied_fill = replace(
            order,
            status=(
                OrderStatus.FILLED
                if applied_fill_quantity(snapshot, order.id) + fill.quantity >= order.quantity
                else order.status
            ),
            filled_quantity=max(
                order.filled_quantity,
                applied_fill_quantity(snapshot, order.id) + fill.quantity,
            ),
            updated_at=stamped.economics_applied_at,
        )
        existing_order = next(
            (
                item
                for item in self.orders.values()
                if item.client_order_id == applied_fill.client_order_id
            ),
            None,
        )
        if existing_order is not None:
            self.orders.pop(existing_order.id, None)
        self.orders[applied_fill.id] = applied_fill
        parent = fill_projection_deployment(snapshot, projected)
        saved = replace(parent, revision=snapshot.deployment.revision + 1)
        self.deployments[saved.id] = saved
        for runtime in projected.instrument_runtimes:
            self.instrument_runtimes[_position_key(deployment_id, runtime.product_id)] = runtime
        product_id = order.product_id or projected.deployment.product_id
        if projected.position is not None:
            self.positions[_position_key(deployment_id, product_id)] = projected.position
        elif projected.deployment.phase.value == "flat":
            self.positions.pop(_position_key(deployment_id, product_id), None)
        return True, await self.get_deployment(deployment_id)

    async def save_position(
        self,
        position: Position | None,
        *,
        deployment_id: UUID,
        product_id: str | None = None,
    ) -> None:
        """Replace or clear one product book, or every book when the product is omitted."""
        if position is None and product_id is None:
            for key in [item for item in self.positions if item[0] == deployment_id]:
                self.positions.pop(key, None)
            return
        key_product = product_id or (position.product_id if position is not None else "")
        key = _position_key(deployment_id, key_product)
        if position is None:
            self.positions.pop(key, None)
            return
        stamped = position if position.product_id else replace(position, product_id=key_product)
        self.positions[_position_key(deployment_id, stamped.product_id)] = stamped

    async def save_instrument_runtime(
        self, runtime: InstrumentRuntime, *, deployment_id: UUID
    ) -> None:
        """Replace one product overlay row."""
        self.instrument_runtimes[_position_key(deployment_id, runtime.product_id)] = runtime

    async def list_open_orders(self, deployment_id: UUID) -> tuple[Order, ...]:
        """Return open or unknown orders that the runtime must observe."""
        watch = {OrderStatus.OPEN, OrderStatus.UNKNOWN, OrderStatus.PENDING}
        return tuple(
            order
            for order in self.orders.values()
            if order.deployment_id == deployment_id and order.status in watch
        )

    async def get_intent_by_idempotency_key(self, idempotency_key: str) -> OrderIntent | None:
        """Return the intent recorded under one client idempotency key, if any."""
        return next(
            (
                intent
                for intent in self.intents.values()
                if intent.idempotency_key == idempotency_key
            ),
            None,
        )


def _focused_position(positions: tuple[Position, ...], primary_product_id: str) -> Position | None:
    """Return the primary book, or the sole book, for legacy snapshot.position readers."""
    if not positions:
        return None
    if len(positions) == 1:
        return positions[0]
    for position in positions:
        if position.product_id in {"", primary_product_id}:
            return position
    return None
