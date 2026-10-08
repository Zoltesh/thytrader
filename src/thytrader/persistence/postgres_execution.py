"""PostgreSQL repository for paper and live execution records.

Row mappers and column-value builders live in
:mod:`thytrader.persistence.postgres_execution_rows`, snapshot assembly in
:mod:`thytrader.persistence.postgres_execution_snapshots`, and the statements the store's
methods execute in :mod:`thytrader.persistence.postgres_execution_statements`; this module
owns every connection and transaction. Names other modules import or patch from here are
re-exported (``__all__``).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from thytrader.fleet_control.commands import confirmed_command
from thytrader.persistence.postgres_execution_rows import (
    _deployment_from_row,
    _deployment_values,
    _fill_from_row,
    _instrument_runtime_upsert,
    _intent_from_row,
    _mutable_deployment_values,
    _order_from_row,
    _order_values,
    _primary_runtime_mirror,
    _twin_link_from_row,
)
from thytrader.persistence.postgres_execution_snapshots import _snapshot, _summary_snapshot
from thytrader.persistence.postgres_execution_statements import (
    _applied_order_upsert,
    _breaker_pause_update,
    _deployment_select,
    _fill_insert,
    _fills_page_select,
    _intent_insert,
    _locked_deployment_select,
    _order_upsert,
    _orders_page_select,
    _position_fee_fills_select,
    _position_insert,
    _twin_link_select,
    _worker_lease_update,
)
from thytrader.persistence.postgres_fleet_admission import refuse_postgres_entry
from thytrader.persistence.schema import (
    deployment_twin_links,
    deployments,
    execution_fills,
    execution_orders,
    execution_positions,
    fleet_entry_inhibition,
    order_intents,
)
from thytrader.trading.fill_ledger import (
    applied_fill_quantity,
    fill_projection_deployment,
    project_fill_economics,
)
from thytrader.trading.ids import utc_now
from thytrader.trading.ledger import (
    LedgerFill,
    remaining_position_entry_fees,
)
from thytrader.trading.models import (
    Deployment,
    DeploymentSnapshot,
    DeploymentSummarySnapshot,
    ExecutionConflictError,
    ExecutionStoreError,
    Fill,
    InstrumentRuntime,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderSide,
    OrderStatus,
    PaginatedFills,
    PaginatedOrders,
    Position,
)
from thytrader.trading.pagination import (
    encode_cursor,
    encode_order_cursor,
)
from thytrader.trading.twins import DeploymentTwinLink, TwinConflictError, comparable_twins

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from thytrader.fleet_control.models import (
        ExpectedTarget,
        FleetAction,
        FleetModeScope,
        TargetResult,
    )
    from thytrader.strategies.snapshots import StrategySnapshot


__all__ = ["PostgresExecutionStore", "_deployment_values", "_snapshot"]


class PostgresExecutionStore:
    """Persist execution records in PostgreSQL using exact decimal strings."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the store to a managed async engine."""
        self._engine = engine

    async def get_twin_link(self, deployment_id: UUID) -> DeploymentTwinLink | None:
        """Read the saved partner of either member without execution histories."""
        try:
            async with self._engine.connect() as conn:
                row = (
                    (await conn.execute(_twin_link_select(deployment_id, deployment_id)))
                    .mappings()
                    .first()
                )
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Twin link storage is unavailable.") from error
        return _twin_link_from_row(row) if row is not None else None

    async def list_twin_links(self) -> tuple[DeploymentTwinLink, ...]:
        """Read explicit pairs newest-linked first, with a stable UUID tiebreaker."""
        try:
            async with self._engine.connect() as conn:
                rows = (
                    (
                        await conn.execute(
                            select(deployment_twin_links).order_by(
                                deployment_twin_links.c.linked_at.desc(),
                                deployment_twin_links.c.paper_deployment_id.desc(),
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Twin link storage is unavailable.") from error
        return tuple(_twin_link_from_row(row) for row in rows)

    async def link_twins(
        self,
        deployment_id: UUID,
        counterpart_id: UUID,
        *,
        snapshots: tuple[StrategySnapshot, StrategySnapshot] | None = None,
    ) -> DeploymentTwinLink:
        """Serialize competing partners with ordered row locks in a short transaction."""
        try:
            async with self._engine.begin() as conn:
                books = await _lock_twin_books(conn, deployment_id, counterpart_id)
                paper, live = comparable_twins(
                    books[deployment_id], books[counterpart_id], snapshots=snapshots
                )
                rows = (await conn.execute(_twin_link_select(paper.id, live.id))).mappings().all()
                for row in rows:
                    current = _twin_link_from_row(row)
                    if (
                        current.paper_deployment_id == paper.id
                        and current.live_deployment_id == live.id
                    ):
                        return current
                    raise TwinConflictError(
                        "A bot already has a twin. Unlink its current partner first."
                    )
                link = DeploymentTwinLink(paper.id, live.id, utc_now())
                await conn.execute(
                    insert(deployment_twin_links).values(
                        paper_deployment_id=paper.id,
                        live_deployment_id=live.id,
                        linked_at=link.linked_at,
                    )
                )
                return link
        except IntegrityError as error:
            raise TwinConflictError("A bot already has a twin. Read its current link.") from error
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Twin link storage is unavailable.") from error

    async def unlink_twins(self, deployment_id: UUID, counterpart_id: UUID) -> None:
        """Delete only the expected pair under the same ordered locks as linking."""
        try:
            async with self._engine.begin() as conn:
                await _lock_twin_books(conn, deployment_id, counterpart_id)
                row = (
                    (await conn.execute(_twin_link_select(deployment_id, deployment_id)))
                    .mappings()
                    .first()
                )
                if row is None:
                    return
                link = _twin_link_from_row(row)
                if link.counterpart(deployment_id) != counterpart_id:
                    raise TwinConflictError(
                        "The twin partner changed. Read the current link before unlinking."
                    )
                await conn.execute(
                    delete(deployment_twin_links).where(
                        deployment_twin_links.c.paper_deployment_id == link.paper_deployment_id
                    )
                )
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Twin link storage is unavailable.") from error

    @property
    def fleet_database_engine(self) -> AsyncEngine:
        """Expose database identity for atomic fleet command/receipt coordination."""
        return self._engine

    async def record_confirmed_fleet_command(
        self,
        connection: AsyncConnection,
        expected: ExpectedTarget,
        action: FleetAction,
        mode: FleetModeScope,
        *,
        now: datetime,
    ) -> TargetResult:
        """Lock, check the confirmed revision, and save within the receipt transaction."""
        row = (
            (await connection.execute(_locked_deployment_select(expected.deployment_id)))
            .mappings()
            .one_or_none()
        )
        current = None if row is None else _deployment_from_row(row)
        updated, receipt = confirmed_command(current, expected, action, mode, now)
        if updated is not None:
            values = _mutable_deployment_values(updated)
            values["revision"] = expected.revision + 1
            result = await connection.execute(
                deployments.update()
                .where(
                    deployments.c.id == expected.deployment_id,
                    deployments.c.revision == expected.revision,
                )
                .values(values)
            )
            if result.rowcount != 1:
                raise ExecutionConflictError("Deployment revision conflict.")
        return receipt

    async def read_entry_inhibition(self) -> dict[str, bool]:
        """Read both durable latch rows; absence or failure cannot admit risk."""
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(select(fleet_entry_inhibition))).mappings().all()
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Entry inhibition storage is unavailable.") from error
        by_mode = {str(row["mode"]): bool(row["inhibited"]) for row in rows}
        if "paper" not in by_mode or "live" not in by_mode:
            raise ExecutionStoreError("Entry inhibition state is incomplete.")
        return by_mode

    async def create_deployment(self, deployment: Deployment) -> Deployment:
        """Insert one new deployment row."""
        statement = insert(deployments).values(_deployment_values(deployment))
        try:
            async with self._engine.begin() as connection:
                await refuse_postgres_entry(connection, mode=deployment.mode.value, action="start")
                await connection.execute(statement)
        except IntegrityError as error:
            if "ux_deployments_active_strategy_mode" in str(error).lower():
                raise ExecutionConflictError(
                    "A running or paused deployment already exists for this strategy and mode."
                ) from error
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        return deployment

    async def get_deployment(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Load one deployment with its related records or fail."""
        try:
            async with self._engine.connect() as connection:
                row = (
                    (await connection.execute(_deployment_select(deployment_id)))
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise ExecutionStoreError("Deployment was not found.")
                return await _snapshot(connection, _deployment_from_row(row))
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error

    async def get_accounting_snapshot(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Read complete shared-book evidence in one repeatable database observation."""
        try:
            async with self._engine.connect() as connection:
                await connection.execution_options(isolation_level="REPEATABLE READ")
                async with connection.begin():
                    row = (
                        (await connection.execute(_deployment_select(deployment_id)))
                        .mappings()
                        .one_or_none()
                    )
                    if row is None:
                        raise ExecutionStoreError("Deployment was not found.")
                    return await _snapshot(connection, _deployment_from_row(row))
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Accounting evidence is unavailable.") from error

    async def get_deployment_summary(self, deployment_id: UUID) -> DeploymentSummarySnapshot:
        """Load positions and overlays without historical orders or fills."""
        try:
            async with self._engine.connect() as connection:
                row = (
                    (await connection.execute(_deployment_select(deployment_id)))
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise ExecutionStoreError("Deployment was not found.")
                deployment = _deployment_from_row(row)
                return await _summary_snapshot(connection, deployment)
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error

    async def list_deployments(
        self, *, limit: int | None = None, offset: int = 0
    ) -> tuple[Deployment, ...]:
        """Return deployments newest-updated first, optionally paginated."""
        statement = select(deployments).order_by(deployments.c.updated_at.desc()).offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        return tuple(_deployment_from_row(row) for row in rows)

    async def get_position_entry_fees(
        self, position: Position, *, product_id: str
    ) -> Decimal | None:
        """Replay bounded applied fills since entry for one product's remaining entry fees."""
        statement = _position_fee_fills_select(position, product_id=product_id)
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        fills = tuple(
            LedgerFill(
                side=OrderSide(row["side"]),
                price=Decimal(row["price"]),
                quantity=Decimal(row["quantity"]),
                fee=Decimal(row["fee"]),
                filled_at=row["filled_at"],
            )
            for row in rows
        )
        return remaining_position_entry_fees(position, fills)

    async def list_fills(
        self, deployment_id: UUID, *, limit: int, cursor: str | None = None
    ) -> PaginatedFills:
        """Return one descending page of fills for one deployment."""
        if limit < 1:
            raise ExecutionStoreError("Fill page limit must be positive.")
        statement = _fills_page_select(deployment_id, limit=limit, cursor=cursor)
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        page_rows = rows[:limit]
        fills: list[Fill] = []
        order_products: list[tuple[UUID, str]] = []
        for row in page_rows:
            fills.append(_fill_from_row(row))
            order_products.append((row["order_id"], row["product_id"] or ""))
        next_cursor = None
        if len(rows) > limit:
            last = page_rows[-1]
            next_cursor = encode_cursor(filled_at=last["filled_at"], row_id=last["id"])
        return PaginatedFills(
            fills=tuple(fills),
            next_cursor=next_cursor,
            order_products=tuple(order_products),
        )

    async def list_orders(
        self, deployment_id: UUID, *, limit: int, cursor: str | None = None
    ) -> PaginatedOrders:
        """Return one descending page of orders for one deployment."""
        if limit < 1:
            raise ExecutionStoreError("Order page limit must be positive.")
        statement = _orders_page_select(deployment_id, limit=limit, cursor=cursor)
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        page = tuple(_order_from_row(row) for row in rows[:limit])
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = encode_order_cursor(created_at=last["created_at"], row_id=last["id"])
        return PaginatedOrders(orders=page, next_cursor=next_cursor)

    async def list_by_strategy(self, strategy_id: str) -> tuple[Deployment, ...]:
        """Return deployments for one strategy identity, newest-updated first."""
        statement = (
            select(deployments)
            .where(deployments.c.strategy_id == strategy_id)
            .order_by(deployments.c.updated_at.desc())
        )
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        return tuple(_deployment_from_row(row) for row in rows)

    async def list_by_strategy_ids(
        self, strategy_ids: Sequence[str]
    ) -> dict[str, tuple[Deployment, ...]]:
        """Return every deployment grouped per requested strategy identity."""
        if not strategy_ids:
            return {}
        statement = (
            select(deployments)
            .where(deployments.c.strategy_id.in_(strategy_ids))
            .order_by(deployments.c.strategy_id, deployments.c.updated_at.desc())
        )
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        grouped: dict[str, list[Deployment]] = {identity: [] for identity in strategy_ids}
        for row in rows:
            deployment = _deployment_from_row(row)
            grouped[str(row["strategy_id"])].append(deployment)
        return {identity: tuple(items) for identity, items in grouped.items()}

    async def save_deployment(
        self,
        deployment: Deployment,
        *,
        expected_revision: int | None = None,
        instrument_runtime: InstrumentRuntime | None = None,
    ) -> Deployment:
        """Gate parent and optional product runtime in one transaction with zero partial effects."""
        if instrument_runtime is not None and expected_revision is None:
            raise ExecutionStoreError("Atomic runtime saves require an expected revision.")
        values = _mutable_deployment_values(deployment)
        values["revision"] = deployments.c.revision + 1
        statement = deployments.update().where(deployments.c.id == deployment.id)
        if expected_revision is not None:
            statement = statement.where(deployments.c.revision == expected_revision)
        statement = statement.values(values).returning(deployments)
        try:
            async with self._engine.begin() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
                if row is None:
                    if expected_revision is not None:
                        raise ExecutionConflictError("Deployment revision conflict.")
                    raise ExecutionStoreError("Deployment was not found.")
                if instrument_runtime is not None:
                    await connection.execute(
                        _instrument_runtime_upsert(instrument_runtime, deployment.id)
                    )
                else:
                    await connection.execute(_primary_runtime_mirror(deployment))
                return _deployment_from_row(row)
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error

    async def save_breaker_pause(
        self,
        deployment_id: UUID,
        *,
        expected_revision: int,
        detail: str,
        daily_loss_latched: bool = False,
    ) -> Deployment:
        """CAS only breaker-owned metadata; preserve economics, lifecycle and runtime rows."""
        statement = _breaker_pause_update(
            deployment_id,
            expected_revision=expected_revision,
            detail=detail,
            daily_loss_latched=daily_loss_latched,
        )
        try:
            async with self._engine.begin() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
                if row is None:
                    raise ExecutionConflictError("Deployment revision conflict.")
                return _deployment_from_row(row)
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error

    async def acquire_worker_lease(
        self,
        deployment_id: UUID,
        *,
        holder: str,
        now: datetime,
        ttl: timedelta,
    ) -> Deployment | None:
        """Acquire or renew a fenced worker lease in a short UPDATE."""
        statement = _worker_lease_update(deployment_id, holder=holder, now=now, ttl=ttl)
        try:
            async with self._engine.begin() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        if row is None:
            return None
        return _deployment_from_row(row)

    async def save_intent(self, intent: OrderIntent) -> OrderIntent:
        """Insert one order intent before venue submission."""
        statement = _intent_insert(intent)
        try:
            async with self._engine.begin() as connection:
                if intent.purpose is IntentPurpose.ENTRY:
                    mode = await connection.scalar(
                        select(deployments.c.mode).where(deployments.c.id == intent.deployment_id)
                    )
                    if not isinstance(mode, str):
                        raise ExecutionStoreError(
                            "Entry deployment mode is unavailable; refusing risk."
                        )
                    await refuse_postgres_entry(connection, mode=mode, action="entry")
                await connection.execute(statement)
        except ExecutionConflictError:
            raise
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        return intent

    async def save_order(self, order: Order) -> Order:
        """Insert or replace one venue-visible order snapshot."""
        statement = _order_upsert(order)
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        return order

    async def save_fill(self, fill: Fill) -> Fill:
        """Insert one fill, ignoring exact venue-fill duplicates."""
        statement = _fill_insert(fill, economics_applied_at=fill.economics_applied_at)
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
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
        """Serialize same-book projection, then commit evidence and economics atomically."""
        try:
            async with self._engine.begin() as connection:
                # Lock before child reads/projection. UPDATE-only locking is too late:
                # independent writers would otherwise compute from the same old cash.
                row = (
                    (await connection.execute(_locked_deployment_select(deployment_id)))
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise ExecutionStoreError("Deployment was not found.")
                deployment = _deployment_from_row(row)
                snapshot = await _snapshot(connection, deployment)
                existing = next(
                    (
                        item
                        for item in snapshot.fills
                        if item.venue_fill_id == fill.venue_fill_id
                        and item.deployment_id == fill.deployment_id
                    ),
                    None,
                )
                if existing is not None and existing.economics_applied_at is not None:
                    return False, snapshot
                await connection.execute(_fill_insert(fill, economics_applied_at=None))
                snapshot = await _snapshot(connection, deployment)
                existing = next(
                    (item for item in snapshot.fills if item.venue_fill_id == fill.venue_fill_id),
                    fill,
                )
                if existing.economics_applied_at is not None:
                    return False, snapshot
                projected, stamped = project_fill_economics(
                    snapshot,
                    fill=existing,
                    order=order,
                    cooldown_bars=cooldown_bars,
                    timeframe=timeframe,
                )
                applied_fill = replace(
                    order,
                    status=(
                        OrderStatus.FILLED
                        if applied_fill_quantity(snapshot, order.id) + fill.quantity
                        >= order.quantity
                        else order.status
                    ),
                    filled_quantity=max(
                        order.filled_quantity,
                        applied_fill_quantity(snapshot, order.id) + fill.quantity,
                    ),
                    updated_at=utc_now(),
                )
                order_values = _order_values(applied_fill)
                await connection.execute(_applied_order_upsert(order_values))
                await connection.execute(
                    execution_fills.update()
                    .where(
                        execution_fills.c.deployment_id == fill.deployment_id,
                        execution_fills.c.venue_fill_id == fill.venue_fill_id,
                    )
                    .values(economics_applied_at=stamped.economics_applied_at)
                )
                for runtime in projected.instrument_runtimes:
                    await connection.execute(_instrument_runtime_upsert(runtime, deployment_id))
                parent = fill_projection_deployment(snapshot, projected)
                next_revision = deployment.revision + 1
                deployment_values = _mutable_deployment_values(parent)
                deployment_values["revision"] = next_revision
                await connection.execute(
                    deployments.update()
                    .where(deployments.c.id == deployment_id)
                    .values(deployment_values)
                )
                product_id = order.product_id or deployment.product_id
                await connection.execute(
                    delete(execution_positions).where(
                        execution_positions.c.deployment_id == deployment_id,
                        execution_positions.c.product_id == product_id,
                    )
                )
                if projected.position is not None:
                    position = projected.position
                    stamped_product = position.product_id or product_id
                    await connection.execute(
                        _position_insert(position, stamped_product=stamped_product)
                    )
                refreshed = await _snapshot(connection, replace(parent, revision=next_revision))
                return True, refreshed
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error

    async def save_position(
        self,
        position: Position | None,
        *,
        deployment_id: UUID,
        product_id: str | None = None,
    ) -> None:
        """Replace or clear one product book, or every book when the product is omitted."""
        try:
            async with self._engine.begin() as connection:
                if position is None and product_id is None:
                    await connection.execute(
                        delete(execution_positions).where(
                            execution_positions.c.deployment_id == deployment_id
                        )
                    )
                    return
                key_product = product_id or (position.product_id if position is not None else "")
                await connection.execute(
                    delete(execution_positions).where(
                        execution_positions.c.deployment_id == deployment_id,
                        execution_positions.c.product_id == key_product,
                    )
                )
                if position is not None:
                    stamped_product = position.product_id or key_product
                    await connection.execute(
                        _position_insert(position, stamped_product=stamped_product)
                    )
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error

    async def save_instrument_runtime(
        self, runtime: InstrumentRuntime, *, deployment_id: UUID
    ) -> None:
        """Replace one product overlay row."""
        try:
            async with self._engine.begin() as connection:
                await connection.execute(_instrument_runtime_upsert(runtime, deployment_id))
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error

    async def list_open_orders(self, deployment_id: UUID) -> tuple[Order, ...]:
        """Return open or unknown orders that the runtime must observe."""
        statement = select(execution_orders).where(
            execution_orders.c.deployment_id == deployment_id,
            execution_orders.c.status.in_(
                (OrderStatus.OPEN.value, OrderStatus.UNKNOWN.value, OrderStatus.PENDING.value)
            ),
        )
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        return tuple(_order_from_row(row) for row in rows)

    async def get_intent_by_idempotency_key(self, idempotency_key: str) -> OrderIntent | None:
        """Return the intent recorded under one client idempotency key, if any."""
        statement = select(order_intents).where(order_intents.c.idempotency_key == idempotency_key)
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        if row is None:
            return None
        return _intent_from_row(row)


async def _lock_twin_books(
    conn: AsyncConnection, deployment_id: UUID, counterpart_id: UUID
) -> dict[UUID, Deployment]:
    """Lock both immutable identities in UUID order to avoid cross-pair deadlocks."""
    rows = (
        (
            await conn.execute(
                select(deployments)
                .where(deployments.c.id.in_((deployment_id, counterpart_id)))
                .order_by(deployments.c.id)
                .with_for_update()
            )
        )
        .mappings()
        .all()
    )
    books = {row["id"]: _deployment_from_row(row) for row in rows}
    if deployment_id not in books or counterpart_id not in books:
        raise ExecutionStoreError("Deployment was not found.")
    return books
