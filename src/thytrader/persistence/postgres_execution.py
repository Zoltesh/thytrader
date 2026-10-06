"""PostgreSQL repository for paper and live execution records."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from thytrader.execution.fill_ledger import applied_fill_quantity, project_fill_economics
from thytrader.execution.ids import utc_now
from thytrader.execution.ledger import (
    MAX_POSITION_FEE_FILLS,
    LedgerFill,
    remaining_position_entry_fees,
)
from thytrader.execution.models import (
    Deployment,
    DeploymentBookTotals,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    DeploymentSummarySnapshot,
    ExecutionConflictError,
    ExecutionStoreError,
    Fill,
    InstrumentRuntime,
    IntentOrigin,
    IntentPurpose,
    LifecycleCommand,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    PaginatedFills,
    PaginatedOrders,
    Position,
    PositionSide,
    RuntimePhase,
)
from thytrader.execution.pagination import (
    decode_cursor,
    decode_order_cursor,
    encode_cursor,
    encode_order_cursor,
)
from thytrader.execution.twins import DeploymentTwinLink, TwinConflictError, comparable_twins
from thytrader.fleet_control.admission import latch_table_missing, refuse_postgres_entry
from thytrader.fleet_control.inventory import InventoryPage
from thytrader.persistence.schema import (
    deployment_twin_links,
    deployments,
    execution_fills,
    execution_instrument_state,
    execution_orders,
    execution_positions,
    fleet_entry_inhibition,
    order_intents,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from thytrader.strategies.snapshots import StrategySnapshot


def _decimal(value: str | None) -> Decimal | None:
    """Parse one stored decimal string, preserving absence."""
    return None if value is None else Decimal(value)


def _lifecycle_command(value: object) -> LifecycleCommand:
    """Parse a stored lifecycle command, defaulting to none for pre-0035 rows."""
    if value is None or value == "":
        return LifecycleCommand.NONE
    return LifecycleCommand(str(value))


def _text(value: Decimal | None) -> str | None:
    """Render one optional Decimal as canonical plain text."""
    return None if value is None else format(value, "f")


def _optional_uuid(value: object) -> UUID | None:
    """Parse an optional stored identity (``portfolio_id`` is text in PostgreSQL)."""
    return None if value is None else UUID(str(value))


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
                    (
                        await conn.execute(
                            select(deployment_twin_links).where(
                                or_(
                                    deployment_twin_links.c.paper_deployment_id == deployment_id,
                                    deployment_twin_links.c.live_deployment_id == deployment_id,
                                )
                            )
                        )
                    )
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
                rows = (
                    (
                        await conn.execute(
                            select(deployment_twin_links).where(
                                or_(
                                    deployment_twin_links.c.paper_deployment_id == paper.id,
                                    deployment_twin_links.c.live_deployment_id == live.id,
                                )
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
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
                    (
                        await conn.execute(
                            select(deployment_twin_links).where(
                                or_(
                                    deployment_twin_links.c.paper_deployment_id == deployment_id,
                                    deployment_twin_links.c.live_deployment_id == deployment_id,
                                )
                            )
                        )
                    )
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

    async def read_entry_inhibition(self) -> dict[str, bool]:
        """Read the durable latch. A missing table means the migration is not applied."""
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(select(fleet_entry_inhibition))).mappings().all()
        except SQLAlchemyError as error:
            if latch_table_missing(error):
                return {"paper": False, "live": False}
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
                    (
                        await connection.execute(
                            select(deployments).where(deployments.c.id == deployment_id)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise ExecutionStoreError("Deployment was not found.")
                return await _snapshot(connection, _deployment_from_row(row))
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error

    async def get_deployment_summary(self, deployment_id: UUID) -> DeploymentSummarySnapshot:
        """Load positions and overlays without historical orders or fills."""
        try:
            async with self._engine.connect() as connection:
                row = (
                    (
                        await connection.execute(
                            select(deployments).where(deployments.c.id == deployment_id)
                        )
                    )
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

    async def list_stable_inventory(
        self,
        *,
        limit: int,
        offset: int,
        strategy_id: UUID | None,
        as_of: datetime,
    ) -> InventoryPage:
        """Return one created-at snapshot page. Updates cannot move a row between pages."""
        if limit < 1 or offset < 0:
            raise ExecutionStoreError("Inventory page bounds are invalid.")
        conditions = [deployments.c.created_at <= as_of]
        if strategy_id is not None:
            conditions.append(deployments.c.strategy_id == str(strategy_id))
        count_statement = select(func.count()).select_from(deployments).where(*conditions)
        page_statement = (
            select(deployments)
            .where(*conditions)
            .order_by(deployments.c.created_at.desc(), deployments.c.id.desc())
            .offset(offset)
            .limit(limit)
        )
        try:
            async with self._engine.connect() as connection:
                total = int(await connection.scalar(count_statement) or 0)
                rows = (await connection.execute(page_statement)).mappings().all()
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        page = tuple(_deployment_from_row(row) for row in rows)
        return InventoryPage(
            deployments=page,
            limit=limit,
            offset=offset,
            returned=len(page),
            total=total,
            has_more=offset + len(page) < total,
            as_of=as_of,
        )

    async def get_position_entry_fees(
        self, position: Position, *, product_id: str
    ) -> Decimal | None:
        """Replay bounded applied fills since entry for one product's remaining entry fees."""
        statement = (
            select(execution_fills, execution_orders.c.side)
            .join(execution_orders, execution_fills.c.order_id == execution_orders.c.id)
            .join(deployments, execution_fills.c.deployment_id == deployments.c.id)
            .where(
                execution_fills.c.deployment_id == position.deployment_id,
                execution_orders.c.deployment_id == position.deployment_id,
                execution_fills.c.filled_at >= position.entered_bar,
                execution_fills.c.economics_applied_at.is_not(None),
                func.coalesce(
                    func.nullif(execution_orders.c.product_id, ""), deployments.c.product_id
                )
                == product_id,
            )
            .order_by(execution_fills.c.filled_at.asc(), execution_fills.c.venue_fill_id.asc())
            .limit(MAX_POSITION_FEE_FILLS + 1)
        )
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
        statement = (
            select(execution_fills, execution_orders.c.product_id)
            .join(execution_orders, execution_fills.c.order_id == execution_orders.c.id)
            .where(execution_fills.c.deployment_id == deployment_id)
            .order_by(execution_fills.c.filled_at.desc(), execution_fills.c.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            try:
                filled_at, row_id = decode_cursor(cursor)
            except ValueError as error:
                raise ExecutionStoreError("Invalid pagination cursor.") from error
            statement = statement.where(
                or_(
                    execution_fills.c.filled_at < filled_at,
                    and_(
                        execution_fills.c.filled_at == filled_at,
                        execution_fills.c.id < row_id,
                    ),
                )
            )
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
        statement = (
            select(execution_orders)
            .where(execution_orders.c.deployment_id == deployment_id)
            .order_by(execution_orders.c.created_at.desc(), execution_orders.c.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            try:
                created_at, row_id = decode_order_cursor(cursor)
            except ValueError as error:
                raise ExecutionStoreError("Invalid pagination cursor.") from error
            statement = statement.where(
                or_(
                    execution_orders.c.created_at < created_at,
                    and_(
                        execution_orders.c.created_at == created_at,
                        execution_orders.c.id < row_id,
                    ),
                )
            )
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
    ) -> Deployment:
        """Replace mutable runtime fields for one existing deployment."""
        next_revision = deployment.revision + 1
        values = _mutable_deployment_values(deployment)
        values["revision"] = next_revision
        statement = deployments.update().where(deployments.c.id == deployment.id)
        if expected_revision is not None:
            statement = statement.where(deployments.c.revision == expected_revision)
        statement = statement.values(values)
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        if result.rowcount != 1:
            if expected_revision is not None:
                raise ExecutionConflictError("Deployment revision conflict.")
            raise ExecutionStoreError("Deployment was not found.")
        return replace(deployment, revision=next_revision)

    async def acquire_worker_lease(
        self,
        deployment_id: UUID,
        *,
        holder: str,
        now: datetime,
        ttl: timedelta,
    ) -> Deployment | None:
        """Acquire or renew a fenced worker lease in a short UPDATE."""
        expires_at = now + ttl
        statement = (
            deployments.update()
            .where(deployments.c.id == deployment_id)
            .where(
                or_(
                    deployments.c.worker_lease_holder.is_(None),
                    deployments.c.worker_lease_expires_at.is_(None),
                    deployments.c.worker_lease_expires_at <= now,
                    deployments.c.worker_lease_holder == holder,
                )
            )
            .values(
                worker_lease_holder=holder,
                worker_lease_expires_at=expires_at,
                revision=deployments.c.revision + 1,
            )
            .returning(deployments)
        )
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
        statement = insert(order_intents).values(
            id=intent.id,
            deployment_id=intent.deployment_id,
            client_order_id=intent.client_order_id,
            purpose=intent.purpose.value,
            side=intent.side.value,
            kind=intent.kind.value,
            price=_text(intent.price),
            stop_trigger_price=_text(intent.stop_trigger_price),
            take_profit_price=_text(intent.take_profit_price),
            quantity=format(intent.quantity, "f"),
            candle_starts_at=intent.candle_starts_at,
            status=intent.status.value,
            origin=intent.origin.value,
            idempotency_key=intent.idempotency_key,
            product_id=intent.product_id,
            created_at=intent.created_at,
        )
        try:
            async with self._engine.begin() as connection:
                if intent.purpose is IntentPurpose.ENTRY:
                    mode = await connection.scalar(
                        select(deployments.c.mode).where(deployments.c.id == intent.deployment_id)
                    )
                    if isinstance(mode, str):
                        await refuse_postgres_entry(connection, mode=mode, action="entry")
                await connection.execute(statement)
        except ExecutionConflictError:
            raise
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        return intent

    async def save_order(self, order: Order) -> Order:
        """Insert or replace one venue-visible order snapshot."""
        values = _order_values(order)
        statement = insert(execution_orders).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[execution_orders.c.client_order_id],
            set_={
                "venue_order_id": statement.excluded.venue_order_id,
                "status": statement.excluded.status,
                "filled_quantity": statement.excluded.filled_quantity,
                "reject_reason": statement.excluded.reject_reason,
                "updated_at": statement.excluded.updated_at,
                "price": statement.excluded.price,
                "stop_trigger_price": statement.excluded.stop_trigger_price,
                "take_profit_price": statement.excluded.take_profit_price,
                "quantity": statement.excluded.quantity,
                "parent_order_id": statement.excluded.parent_order_id,
                "attached_child_venue_order_id": statement.excluded.attached_child_venue_order_id,
                "venue_observed_at": statement.excluded.venue_observed_at,
                "pyramid_add": statement.excluded.pyramid_add,
            },
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        return order

    async def save_fill(self, fill: Fill) -> Fill:
        """Insert one fill, ignoring exact venue-fill duplicates."""
        statement = (
            insert(execution_fills)
            .values(
                id=fill.id,
                deployment_id=fill.deployment_id,
                order_id=fill.order_id,
                venue_fill_id=fill.venue_fill_id,
                price=format(fill.price, "f"),
                quantity=format(fill.quantity, "f"),
                fee=format(fill.fee, "f"),
                filled_at=fill.filled_at,
                economics_applied_at=fill.economics_applied_at,
            )
            .on_conflict_do_nothing(constraint="ux_execution_fills_venue")
        )
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
        """Insert fill evidence and apply economics in one database transaction."""
        try:
            async with self._engine.begin() as connection:
                row = (
                    (
                        await connection.execute(
                            select(deployments).where(deployments.c.id == deployment_id)
                        )
                    )
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
                insert_statement = (
                    insert(execution_fills)
                    .values(
                        id=fill.id,
                        deployment_id=fill.deployment_id,
                        order_id=fill.order_id,
                        venue_fill_id=fill.venue_fill_id,
                        price=format(fill.price, "f"),
                        quantity=format(fill.quantity, "f"),
                        fee=format(fill.fee, "f"),
                        filled_at=fill.filled_at,
                        economics_applied_at=None,
                    )
                    .on_conflict_do_nothing(constraint="ux_execution_fills_venue")
                )
                await connection.execute(insert_statement)
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
                    status=OrderStatus.FILLED,
                    filled_quantity=max(
                        order.filled_quantity,
                        applied_fill_quantity(snapshot, order.id) + fill.quantity,
                    ),
                    updated_at=utc_now(),
                )
                order_values = _order_values(applied_fill)
                await connection.execute(
                    insert(execution_orders)
                    .values(order_values)
                    .on_conflict_do_update(
                        index_elements=[execution_orders.c.client_order_id],
                        set_={
                            "status": order_values["status"],
                            "filled_quantity": order_values["filled_quantity"],
                            "updated_at": order_values["updated_at"],
                        },
                    )
                )
                await connection.execute(
                    execution_fills.update()
                    .where(
                        execution_fills.c.deployment_id == fill.deployment_id,
                        execution_fills.c.venue_fill_id == fill.venue_fill_id,
                    )
                    .values(economics_applied_at=stamped.economics_applied_at)
                )
                next_revision = deployment.revision + 1
                deployment_values = _mutable_deployment_values(projected.deployment)
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
                        insert(execution_positions).values(
                            deployment_id=position.deployment_id,
                            product_id=stamped_product,
                            quantity=format(position.quantity, "f"),
                            entry_price=format(position.entry_price, "f"),
                            stop_price=format(position.stop_price, "f"),
                            target_price=_text(position.target_price),
                            entered_bar=position.entered_bar,
                            trail_extreme=_text(position.trail_extreme),
                            side=position.side.value,
                            add_count=position.add_count,
                            updated_at=position.updated_at,
                            signal_exit_bar=position.signal_exit_bar,
                        )
                    )
                refreshed = await _snapshot(
                    connection, replace(projected.deployment, revision=next_revision)
                )
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
                        insert(execution_positions).values(
                            deployment_id=position.deployment_id,
                            product_id=stamped_product,
                            quantity=format(position.quantity, "f"),
                            entry_price=format(position.entry_price, "f"),
                            stop_price=format(position.stop_price, "f"),
                            target_price=_text(position.target_price),
                            entered_bar=position.entered_bar,
                            trail_extreme=_text(position.trail_extreme),
                            side=position.side.value,
                            add_count=position.add_count,
                            updated_at=position.updated_at,
                            signal_exit_bar=position.signal_exit_bar,
                        )
                    )
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error

    async def save_instrument_runtime(
        self, runtime: InstrumentRuntime, *, deployment_id: UUID
    ) -> None:
        """Replace one product overlay row."""
        values = {
            "deployment_id": deployment_id,
            "product_id": runtime.product_id,
            "phase": runtime.phase.value,
            "last_evaluated_bar": runtime.last_evaluated_bar,
            "last_signal": runtime.last_signal,
            "pending_entry_bars": runtime.pending_entry_bars,
            "bars_held": runtime.bars_held,
            "cooldown_bars_remaining": runtime.cooldown_bars_remaining,
            "pending_stop_price": _text(runtime.pending_stop_price),
            "pending_target_price": _text(runtime.pending_target_price),
        }
        statement = insert(execution_instrument_state).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[
                execution_instrument_state.c.deployment_id,
                execution_instrument_state.c.product_id,
            ],
            set_={
                "phase": statement.excluded.phase,
                "last_evaluated_bar": statement.excluded.last_evaluated_bar,
                "last_signal": statement.excluded.last_signal,
                "pending_entry_bars": statement.excluded.pending_entry_bars,
                "bars_held": statement.excluded.bars_held,
                "cooldown_bars_remaining": statement.excluded.cooldown_bars_remaining,
                "pending_stop_price": statement.excluded.pending_stop_price,
                "pending_target_price": statement.excluded.pending_target_price,
            },
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
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


_STRATEGY_IDENTITY_COLUMNS = (
    "strategy_fingerprint",
    "strategy_id",
    "strategy_name",
    "portfolio_id",
)


def _mutable_deployment_values(deployment: Deployment) -> dict[str, object]:
    """Map runtime fields for UPDATE, never rewriting the book's strategy identity.

    ``strategy_id`` only ever changes through ``ON DELETE SET NULL`` when a stopped
    live book's strategy is deleted (ADR 0082); a worker holding an older in-memory
    copy must not write the deleted id back. ``portfolio_id`` is fixed when a portfolio
    start creates the book and only clears through ``ON DELETE SET NULL`` (ADR 0091).
    """
    values = _deployment_values(deployment)
    for column in _STRATEGY_IDENTITY_COLUMNS:
        values.pop(column)
    return values


def _deployment_values(deployment: Deployment) -> dict[str, object]:
    """Map one deployment into insertable column values."""
    return {
        "id": deployment.id,
        "strategy_fingerprint": deployment.strategy_fingerprint,
        "strategy_id": None if deployment.strategy_id is None else str(deployment.strategy_id),
        "strategy_name": deployment.strategy_name,
        "portfolio_id": None if deployment.portfolio_id is None else str(deployment.portfolio_id),
        "product_id": deployment.product_id,
        "mode": deployment.mode.value,
        "status": deployment.status.value,
        "kind": deployment.kind.value,
        "timeframe": deployment.timeframe,
        "paper_starting_cash": _text(deployment.paper_starting_cash),
        "paper_maker_fee_rate": _text(deployment.paper_maker_fee_rate),
        "paper_taker_fee_rate": _text(deployment.paper_taker_fee_rate),
        "cash": format(deployment.cash, "f"),
        "phase": deployment.phase.value,
        "last_evaluated_bar": deployment.last_evaluated_bar,
        "last_signal": deployment.last_signal,
        "mismatch_detail": deployment.mismatch_detail,
        "pending_entry_bars": deployment.pending_entry_bars,
        "bars_held": deployment.bars_held,
        "cooldown_bars_remaining": deployment.cooldown_bars_remaining,
        "pending_stop_price": _text(deployment.pending_stop_price),
        "pending_target_price": _text(deployment.pending_target_price),
        "revision": deployment.revision,
        "worker_lease_holder": deployment.worker_lease_holder,
        "worker_lease_expires_at": deployment.worker_lease_expires_at,
        "lifecycle_command": deployment.lifecycle_command.value,
        "allocated_capital": _text(deployment.allocated_capital),
        "venue_available_quote": _text(deployment.venue_available_quote),
        "reserved_buying_power": _text(deployment.reserved_buying_power),
        "inventory_cost": _text(deployment.inventory_cost),
        "performance_equity": _text(deployment.performance_equity),
        "performance_capital_quote": _text(deployment.performance_capital_quote),
        "performance_maximum_drawdown_fraction": _text(
            deployment.performance_maximum_drawdown_fraction
        ),
        "initial_equity": _text(deployment.initial_equity),
        "baseline_equity": _text(deployment.baseline_equity),
        "utc_day_open_equity": _text(deployment.utc_day_open_equity),
        "utc_day_open_at": deployment.utc_day_open_at,
        "high_water_mark_equity": _text(deployment.high_water_mark_equity),
        "daily_loss_latched": deployment.daily_loss_latched,
        "drawdown_latched": deployment.drawdown_latched,
        "last_signal_event_at": deployment.last_signal_event_at,
        "last_signal_processed_at": deployment.last_signal_processed_at,
        "created_at": deployment.created_at,
        "updated_at": deployment.updated_at,
    }


def _order_values(order: Order) -> dict[str, object]:
    """Map one order into insertable column values."""
    return {
        "id": order.id,
        "deployment_id": order.deployment_id,
        "intent_id": order.intent_id,
        "client_order_id": order.client_order_id,
        "venue_order_id": order.venue_order_id,
        "side": order.side.value,
        "kind": order.kind.value,
        "price": _text(order.price),
        "stop_trigger_price": _text(order.stop_trigger_price),
        "take_profit_price": _text(order.take_profit_price),
        "quantity": format(order.quantity, "f"),
        "filled_quantity": format(order.filled_quantity, "f"),
        "status": order.status.value,
        "reject_reason": order.reject_reason,
        "product_id": order.product_id,
        "parent_order_id": order.parent_order_id,
        "attached_child_venue_order_id": order.attached_child_venue_order_id,
        "venue_observed_at": order.venue_observed_at,
        "pyramid_add": order.pyramid_add,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }


def _deployment_from_row(row: RowMapping) -> Deployment:
    """Rehydrate one deployment from a database row."""
    return Deployment(
        id=row["id"],
        strategy_fingerprint=row["strategy_fingerprint"],
        strategy_id=None if row["strategy_id"] is None else UUID(str(row["strategy_id"])),
        strategy_name=row.get("strategy_name"),
        portfolio_id=_optional_uuid(row.get("portfolio_id")),
        product_id=row["product_id"],
        mode=DeploymentMode(row["mode"]),
        status=DeploymentStatus(row["status"]),
        kind=DeploymentKind(row["kind"]) if row["kind"] is not None else DeploymentKind.STRATEGY,
        timeframe=row["timeframe"],
        paper_starting_cash=_decimal(row["paper_starting_cash"]),
        paper_maker_fee_rate=_decimal(row["paper_maker_fee_rate"]),
        paper_taker_fee_rate=_decimal(row["paper_taker_fee_rate"]),
        cash=Decimal(row["cash"]),
        phase=RuntimePhase(row["phase"]),
        last_evaluated_bar=row["last_evaluated_bar"],
        last_signal=row["last_signal"],
        mismatch_detail=row["mismatch_detail"],
        pending_entry_bars=row["pending_entry_bars"],
        bars_held=row["bars_held"],
        cooldown_bars_remaining=row["cooldown_bars_remaining"],
        pending_stop_price=_decimal(row["pending_stop_price"]),
        pending_target_price=_decimal(row["pending_target_price"]),
        revision=int(row["revision"]) if row.get("revision") is not None else 0,
        worker_lease_holder=row.get("worker_lease_holder"),
        worker_lease_expires_at=row.get("worker_lease_expires_at"),
        lifecycle_command=_lifecycle_command(row.get("lifecycle_command")),
        allocated_capital=_decimal(row.get("allocated_capital")),
        venue_available_quote=_decimal(row.get("venue_available_quote")),
        reserved_buying_power=_decimal(row.get("reserved_buying_power")),
        inventory_cost=_decimal(row.get("inventory_cost")),
        performance_equity=_decimal(row.get("performance_equity")),
        performance_capital_quote=_decimal(row.get("performance_capital_quote")),
        performance_maximum_drawdown_fraction=_decimal(
            row.get("performance_maximum_drawdown_fraction")
        ),
        initial_equity=_decimal(row.get("initial_equity")),
        baseline_equity=_decimal(row.get("baseline_equity")),
        utc_day_open_equity=_decimal(row.get("utc_day_open_equity")),
        utc_day_open_at=row.get("utc_day_open_at"),
        high_water_mark_equity=_decimal(row.get("high_water_mark_equity")),
        daily_loss_latched=bool(row.get("daily_loss_latched", False)),
        drawdown_latched=bool(row.get("drawdown_latched", False)),
        last_signal_event_at=row.get("last_signal_event_at"),
        last_signal_processed_at=row.get("last_signal_processed_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _order_from_row(row: RowMapping) -> Order:
    """Rehydrate one order from a database row."""
    return Order(
        id=row["id"],
        deployment_id=row["deployment_id"],
        intent_id=row["intent_id"],
        client_order_id=row["client_order_id"],
        venue_order_id=row["venue_order_id"],
        side=OrderSide(row["side"]),
        kind=OrderKind(row["kind"]),
        price=_decimal(row["price"]),
        stop_trigger_price=_decimal(row["stop_trigger_price"]),
        take_profit_price=_decimal(row["take_profit_price"]),
        quantity=Decimal(row["quantity"]),
        filled_quantity=Decimal(row["filled_quantity"]),
        status=OrderStatus(row["status"]),
        reject_reason=row["reject_reason"],
        product_id=row["product_id"] if row["product_id"] is not None else "",
        parent_order_id=row.get("parent_order_id"),
        attached_child_venue_order_id=row.get("attached_child_venue_order_id"),
        venue_observed_at=row.get("venue_observed_at"),
        pyramid_add=bool(row.get("pyramid_add", False)),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _intent_from_row(row: RowMapping) -> OrderIntent:
    """Rehydrate one intent from a database row."""
    return OrderIntent(
        id=row["id"],
        deployment_id=row["deployment_id"],
        client_order_id=row["client_order_id"],
        purpose=IntentPurpose(row["purpose"]),
        side=OrderSide(row["side"]),
        kind=OrderKind(row["kind"]),
        price=_decimal(row["price"]),
        stop_trigger_price=_decimal(row["stop_trigger_price"]),
        take_profit_price=_decimal(row["take_profit_price"]),
        quantity=Decimal(row["quantity"]),
        candle_starts_at=row["candle_starts_at"],
        status=OrderStatus(row["status"]),
        origin=IntentOrigin(row["origin"]) if row["origin"] is not None else IntentOrigin.RUNTIME,
        idempotency_key=row["idempotency_key"],
        product_id=row["product_id"] if row["product_id"] is not None else "",
        created_at=row["created_at"],
    )


def _fill_from_row(row: RowMapping) -> Fill:
    """Rehydrate one fill from a database row."""
    return Fill(
        id=row["id"],
        deployment_id=row["deployment_id"],
        order_id=row["order_id"],
        venue_fill_id=row["venue_fill_id"],
        price=Decimal(row["price"]),
        quantity=Decimal(row["quantity"]),
        fee=Decimal(row["fee"]),
        filled_at=row["filled_at"],
        economics_applied_at=row.get("economics_applied_at"),
    )


def _position_from_row(row: RowMapping) -> Position:
    """Rehydrate one position from a database row."""
    return Position(
        deployment_id=row["deployment_id"],
        quantity=Decimal(row["quantity"]),
        entry_price=Decimal(row["entry_price"]),
        stop_price=Decimal(row["stop_price"]),
        target_price=_decimal(row["target_price"]),
        entered_bar=row["entered_bar"],
        trail_extreme=_decimal(row["trail_extreme"]),
        side=PositionSide(row["side"]) if row["side"] is not None else PositionSide.LONG,
        product_id=row["product_id"] if row["product_id"] is not None else "",
        add_count=int(row["add_count"]) if row["add_count"] is not None else 1,
        updated_at=row["updated_at"],
        signal_exit_bar=row.get("signal_exit_bar"),
    )


def _runtime_from_row(row: RowMapping) -> InstrumentRuntime:
    """Rehydrate one per-product runtime overlay from a database row."""
    return InstrumentRuntime(
        product_id=row["product_id"],
        phase=RuntimePhase(row["phase"]),
        last_evaluated_bar=row["last_evaluated_bar"],
        last_signal=row["last_signal"],
        pending_entry_bars=row["pending_entry_bars"],
        bars_held=row["bars_held"],
        cooldown_bars_remaining=row["cooldown_bars_remaining"],
        pending_stop_price=_decimal(row["pending_stop_price"]),
        pending_target_price=_decimal(row["pending_target_price"]),
    )


async def _snapshot(connection: AsyncConnection, deployment: Deployment) -> DeploymentSnapshot:
    """Load related records for one already-fetched deployment."""
    intent_rows = (
        (
            await connection.execute(
                select(order_intents)
                .where(order_intents.c.deployment_id == deployment.id)
                .order_by(order_intents.c.created_at.desc())
            )
        )
        .mappings()
        .all()
    )
    order_rows = (
        (
            await connection.execute(
                select(execution_orders)
                .where(execution_orders.c.deployment_id == deployment.id)
                .order_by(execution_orders.c.created_at.desc())
            )
        )
        .mappings()
        .all()
    )
    fill_rows = (
        (
            await connection.execute(
                select(execution_fills)
                .where(execution_fills.c.deployment_id == deployment.id)
                .order_by(execution_fills.c.filled_at.desc())
            )
        )
        .mappings()
        .all()
    )
    position_rows = (
        (
            await connection.execute(
                select(execution_positions).where(
                    execution_positions.c.deployment_id == deployment.id
                )
            )
        )
        .mappings()
        .all()
    )
    runtime_rows = (
        (
            await connection.execute(
                select(execution_instrument_state).where(
                    execution_instrument_state.c.deployment_id == deployment.id
                )
            )
        )
        .mappings()
        .all()
    )
    positions = tuple(_position_from_row(row) for row in position_rows)
    focused = None
    if len(positions) == 1:
        focused = positions[0]
    else:
        focused = next(
            (item for item in positions if item.product_id in {"", deployment.product_id}),
            None,
        )
    return DeploymentSnapshot(
        deployment=deployment,
        position=focused,
        orders=tuple(_order_from_row(row) for row in order_rows),
        fills=tuple(_fill_from_row(row) for row in fill_rows),
        intents=tuple(_intent_from_row(row) for row in intent_rows),
        positions=positions,
        instrument_runtimes=tuple(_runtime_from_row(row) for row in runtime_rows),
    )


async def _summary_snapshot(
    connection: AsyncConnection, deployment: Deployment
) -> DeploymentSummarySnapshot:
    """Load positions, overlays, and counts without historical orders or fills."""
    position_rows = (
        (
            await connection.execute(
                select(execution_positions).where(
                    execution_positions.c.deployment_id == deployment.id
                )
            )
        )
        .mappings()
        .all()
    )
    runtime_rows = (
        (
            await connection.execute(
                select(execution_instrument_state).where(
                    execution_instrument_state.c.deployment_id == deployment.id
                )
            )
        )
        .mappings()
        .all()
    )
    fill_count = int(
        (
            await connection.execute(
                select(func.count())
                .select_from(execution_fills)
                .where(execution_fills.c.deployment_id == deployment.id)
            )
        ).scalar_one()
    )
    working_orders = int(
        (
            await connection.execute(
                select(func.count())
                .select_from(execution_orders)
                .where(execution_orders.c.deployment_id == deployment.id)
                .where(
                    execution_orders.c.status.in_(
                        (
                            OrderStatus.OPEN.value,
                            OrderStatus.PENDING.value,
                            OrderStatus.UNKNOWN.value,
                        )
                    )
                )
            )
        ).scalar_one()
    )
    open_order_rows = (
        (
            await connection.execute(
                select(execution_orders)
                .where(execution_orders.c.deployment_id == deployment.id)
                .where(
                    execution_orders.c.status.in_(
                        (
                            OrderStatus.OPEN.value,
                            OrderStatus.PENDING.value,
                            OrderStatus.UNKNOWN.value,
                        )
                    )
                )
            )
        )
        .mappings()
        .all()
    )
    positions = tuple(_position_from_row(row) for row in position_rows)
    focused = None
    if len(positions) == 1:
        focused = positions[0]
    else:
        focused = next(
            (item for item in positions if item.product_id in {"", deployment.product_id}),
            None,
        )
    return DeploymentSummarySnapshot(
        deployment=deployment,
        position=focused,
        positions=positions,
        instrument_runtimes=tuple(_runtime_from_row(row) for row in runtime_rows),
        book_totals=DeploymentBookTotals(
            open_books=len(positions),
            working_orders=working_orders,
            fill_count=fill_count,
        ),
        open_orders=tuple(_order_from_row(row) for row in open_order_rows),
    )


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


def _twin_link_from_row(row: RowMapping) -> DeploymentTwinLink:
    """Restore one explicit pair from its durable row."""
    return DeploymentTwinLink(
        row["paper_deployment_id"], row["live_deployment_id"], row["linked_at"]
    )
