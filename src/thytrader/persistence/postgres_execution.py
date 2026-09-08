"""PostgreSQL repository for paper and live execution records."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionStoreError,
    Fill,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
)
from thytrader.persistence.schema import (
    deployments,
    execution_fills,
    execution_orders,
    execution_positions,
    order_intents,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


def _decimal(value: str | None) -> Decimal | None:
    """Parse one stored decimal string, preserving absence."""
    return None if value is None else Decimal(value)


def _text(value: Decimal | None) -> str | None:
    """Render one optional Decimal as canonical plain text."""
    return None if value is None else format(value, "f")


class PostgresExecutionStore:
    """Persist execution records in PostgreSQL using exact decimal strings."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the store to a managed async engine."""
        self._engine = engine

    async def create_deployment(self, deployment: Deployment) -> Deployment:
        """Insert one new deployment row."""
        statement = insert(deployments).values(_deployment_values(deployment))
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
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

    async def list_deployments(self) -> tuple[Deployment, ...]:
        """Return every deployment, newest-updated first."""
        statement = select(deployments).order_by(deployments.c.updated_at.desc())
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        return tuple(_deployment_from_row(row) for row in rows)

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

    async def save_deployment(self, deployment: Deployment) -> Deployment:
        """Replace mutable runtime fields for one existing deployment."""
        statement = (
            deployments.update()
            .where(deployments.c.id == deployment.id)
            .values(_deployment_values(deployment))
        )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        if result.rowcount != 1:
            raise ExecutionStoreError("Deployment was not found.")
        return deployment

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
            quantity=format(intent.quantity, "f"),
            candle_starts_at=intent.candle_starts_at,
            status=intent.status.value,
            created_at=intent.created_at,
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
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
                "quantity": statement.excluded.quantity,
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
            )
            .on_conflict_do_nothing(constraint="ux_execution_fills_venue")
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        return fill

    async def save_position(self, position: Position | None, *, deployment_id: UUID) -> None:
        """Replace or clear the single position for one deployment."""
        try:
            async with self._engine.begin() as connection:
                await connection.execute(
                    delete(execution_positions).where(
                        execution_positions.c.deployment_id == deployment_id
                    )
                )
                if position is not None:
                    await connection.execute(
                        insert(execution_positions).values(
                            deployment_id=position.deployment_id,
                            quantity=format(position.quantity, "f"),
                            entry_price=format(position.entry_price, "f"),
                            stop_price=format(position.stop_price, "f"),
                            target_price=format(position.target_price, "f"),
                            entered_bar=position.entered_bar,
                            updated_at=position.updated_at,
                        )
                    )
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


def _deployment_values(deployment: Deployment) -> dict[str, object]:
    """Map one deployment into insertable column values."""
    return {
        "id": deployment.id,
        "strategy_fingerprint": deployment.strategy_fingerprint,
        "strategy_id": str(deployment.strategy_id),
        "product_id": deployment.product_id,
        "mode": deployment.mode.value,
        "status": deployment.status.value,
        "paper_starting_cash": _text(deployment.paper_starting_cash),
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
        "quantity": format(order.quantity, "f"),
        "filled_quantity": format(order.filled_quantity, "f"),
        "status": order.status.value,
        "reject_reason": order.reject_reason,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }


def _deployment_from_row(row: RowMapping) -> Deployment:
    """Rehydrate one deployment from a database row."""
    return Deployment(
        id=row["id"],
        strategy_fingerprint=row["strategy_fingerprint"],
        strategy_id=UUID(str(row["strategy_id"])),
        product_id=row["product_id"],
        mode=DeploymentMode(row["mode"]),
        status=DeploymentStatus(row["status"]),
        paper_starting_cash=_decimal(row["paper_starting_cash"]),
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
        quantity=Decimal(row["quantity"]),
        filled_quantity=Decimal(row["filled_quantity"]),
        status=OrderStatus(row["status"]),
        reject_reason=row["reject_reason"],
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
        quantity=Decimal(row["quantity"]),
        candle_starts_at=row["candle_starts_at"],
        status=OrderStatus(row["status"]),
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
    )


def _position_from_row(row: RowMapping) -> Position:
    """Rehydrate one position from a database row."""
    return Position(
        deployment_id=row["deployment_id"],
        quantity=Decimal(row["quantity"]),
        entry_price=Decimal(row["entry_price"]),
        stop_price=Decimal(row["stop_price"]),
        target_price=Decimal(row["target_price"]),
        entered_bar=row["entered_bar"],
        updated_at=row["updated_at"],
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
    position_row = (
        (
            await connection.execute(
                select(execution_positions).where(
                    execution_positions.c.deployment_id == deployment.id
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    return DeploymentSnapshot(
        deployment=deployment,
        position=None if position_row is None else _position_from_row(position_row),
        orders=tuple(_order_from_row(row) for row in order_rows),
        fills=tuple(_fill_from_row(row) for row in fill_rows),
        intents=tuple(_intent_from_row(row) for row in intent_rows),
    )
