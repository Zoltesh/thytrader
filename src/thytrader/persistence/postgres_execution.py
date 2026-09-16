"""PostgreSQL repository for paper and live execution records."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.execution.ids import utc_now
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionStoreError,
    Fill,
    FillApplication,
    FillApplicationResult,
    InstrumentRuntime,
    IntentOrigin,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
    resolved_product_id,
)
from thytrader.persistence.schema import (
    deployments,
    execution_fills,
    execution_instrument_state,
    execution_orders,
    execution_positions,
    order_intents,
)

if TYPE_CHECKING:
    from sqlalchemy.dialects.postgresql import Insert as PostgresInsert
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
            set_=_order_conflict_set(statement),
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        return order

    async def save_fill(self, fill: Fill) -> Fill:
        """Insert one fill, ignoring exact venue-fill duplicates.

        This path leaves ``applied_at`` unset. Callers that must apply the fill's
        cash/position/order effect use :meth:`apply_fill_effect` instead, which
        completes an unapplied row recorded here atomically with that effect.
        """
        statement = (
            insert(execution_fills)
            .values(**_fill_values(fill), applied_at=None)
            .on_conflict_do_nothing(constraint="ux_execution_fills_venue")
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        return fill

    async def apply_fill_effect(self, application: FillApplication) -> FillApplicationResult:
        """Insert, apply, and mark one fill applied in a single database transaction.

        Dedupes on ``(deployment_id, venue_fill_id)`` and locks the conflicting row
        via ``INSERT ... ON CONFLICT ... RETURNING`` so a concurrent duplicate call
        blocks until this transaction commits or rolls back. When the row already
        carries a non-null ``applied_at`` (a prior call already ran this fill's
        economic effect to completion), this call rolls back and returns
        ``applied=False`` without touching the order, position, or deployment rows.
        """
        fill = application.fill
        insert_fill = (
            insert(execution_fills)
            .values(**_fill_values(fill), applied_at=None)
            .on_conflict_do_update(
                constraint="ux_execution_fills_venue",
                set_={"id": execution_fills.c.id},
            )
            .returning(execution_fills.c.applied_at)
        )
        key_product = resolved_product_id(application.order.product_id, application.deployment)
        try:
            async with self._engine.begin() as connection:
                existing_applied_at = (await connection.execute(insert_fill)).scalar_one()
                if existing_applied_at is not None:
                    return FillApplicationResult(applied=False)
                order_statement = insert(execution_orders).values(_order_values(application.order))
                order_statement = order_statement.on_conflict_do_update(
                    index_elements=[execution_orders.c.client_order_id],
                    set_=_order_conflict_set(order_statement),
                )
                await connection.execute(order_statement)
                await connection.execute(
                    delete(execution_positions).where(
                        execution_positions.c.deployment_id == application.deployment.id,
                        execution_positions.c.product_id == key_product,
                    )
                )
                if not application.clear_position and application.position is not None:
                    await connection.execute(
                        insert(execution_positions).values(
                            _position_values(application.position, fallback_product_id=key_product)
                        )
                    )
                await connection.execute(
                    deployments.update()
                    .where(deployments.c.id == application.deployment.id)
                    .values(_deployment_values(application.deployment))
                )
                await connection.execute(
                    update(execution_fills)
                    .where(
                        execution_fills.c.deployment_id == fill.deployment_id,
                        execution_fills.c.venue_fill_id == fill.venue_fill_id,
                    )
                    .values(applied_at=utc_now())
                )
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        return FillApplicationResult(applied=True)

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
                    await connection.execute(
                        insert(execution_positions).values(
                            _position_values(position, fallback_product_id=key_product)
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


def _deployment_values(deployment: Deployment) -> dict[str, object]:
    """Map one deployment into insertable column values."""
    return {
        "id": deployment.id,
        "strategy_fingerprint": deployment.strategy_fingerprint,
        "strategy_id": None if deployment.strategy_id is None else str(deployment.strategy_id),
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
        "created_at": deployment.created_at,
        "updated_at": deployment.updated_at,
    }


def _order_conflict_set(statement: PostgresInsert) -> dict[str, object]:
    """Return the upsert column set shared by ``save_order`` and ``apply_fill_effect``."""
    return {
        "venue_order_id": statement.excluded.venue_order_id,
        "status": statement.excluded.status,
        "filled_quantity": statement.excluded.filled_quantity,
        "reject_reason": statement.excluded.reject_reason,
        "updated_at": statement.excluded.updated_at,
        "price": statement.excluded.price,
        "stop_trigger_price": statement.excluded.stop_trigger_price,
        "take_profit_price": statement.excluded.take_profit_price,
        "quantity": statement.excluded.quantity,
    }


def _fill_values(fill: Fill) -> dict[str, object]:
    """Map one fill into insertable column values, excluding ``applied_at``."""
    return {
        "id": fill.id,
        "deployment_id": fill.deployment_id,
        "order_id": fill.order_id,
        "venue_fill_id": fill.venue_fill_id,
        "price": format(fill.price, "f"),
        "quantity": format(fill.quantity, "f"),
        "fee": format(fill.fee, "f"),
        "filled_at": fill.filled_at,
    }


def _position_values(position: Position, *, fallback_product_id: str) -> dict[str, object]:
    """Map one position into insertable column values."""
    return {
        "deployment_id": position.deployment_id,
        "product_id": position.product_id or fallback_product_id,
        "quantity": format(position.quantity, "f"),
        "entry_price": format(position.entry_price, "f"),
        "stop_price": format(position.stop_price, "f"),
        "target_price": format(position.target_price, "f"),
        "entered_bar": position.entered_bar,
        "trail_extreme": _text(position.trail_extreme),
        "side": position.side.value,
        "add_count": position.add_count,
        "last_fill_intent_id": position.last_fill_intent_id,
        "updated_at": position.updated_at,
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
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }


def _deployment_from_row(row: RowMapping) -> Deployment:
    """Rehydrate one deployment from a database row."""
    return Deployment(
        id=row["id"],
        strategy_fingerprint=row["strategy_fingerprint"],
        strategy_id=None if row["strategy_id"] is None else UUID(str(row["strategy_id"])),
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
        applied_at=row["applied_at"],
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
        trail_extreme=_decimal(row["trail_extreme"]),
        side=PositionSide(row["side"]) if row["side"] is not None else PositionSide.LONG,
        product_id=row["product_id"] if row["product_id"] is not None else "",
        add_count=int(row["add_count"]) if row["add_count"] is not None else 1,
        last_fill_intent_id=(
            None if row["last_fill_intent_id"] is None else UUID(str(row["last_fill_intent_id"]))
        ),
        updated_at=row["updated_at"],
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
