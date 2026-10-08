"""SQLAlchemy statement builders for the PostgreSQL execution store.

:class:`~thytrader.persistence.postgres_execution.PostgresExecutionStore` owns every
connection and transaction; the builders here only assemble the statements its methods
execute, so the SQL and its bind parameters stay exactly as the methods wrote them inline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import Insert, insert

from thytrader.persistence.postgres_execution_rows import _order_values, _text
from thytrader.persistence.schema import (
    deployment_twin_links,
    deployments,
    execution_fills,
    execution_orders,
    execution_positions,
    order_intents,
)
from thytrader.trading.ids import utc_now
from thytrader.trading.ledger import MAX_POSITION_FEE_FILLS
from thytrader.trading.models import DeploymentStatus, ExecutionStoreError
from thytrader.trading.pagination import decode_cursor, decode_order_cursor

if TYPE_CHECKING:
    from datetime import datetime, timedelta
    from uuid import UUID

    from sqlalchemy import Select
    from sqlalchemy.sql.dml import ReturningUpdate

    from thytrader.trading.models import Fill, Order, OrderIntent, Position


def _twin_link_select(paper_id: UUID, live_id: UUID) -> Select[Any]:
    """Select the saved pair holding either the paper or the live member."""
    return select(deployment_twin_links).where(
        or_(
            deployment_twin_links.c.paper_deployment_id == paper_id,
            deployment_twin_links.c.live_deployment_id == live_id,
        )
    )


def _deployment_select(deployment_id: UUID) -> Select[Any]:
    """Select one deployment row."""
    return select(deployments).where(deployments.c.id == deployment_id)


def _locked_deployment_select(deployment_id: UUID) -> Select[Any]:
    """Select and row-lock one deployment so same-book writers serialize on it."""
    return select(deployments).where(deployments.c.id == deployment_id).with_for_update()


def _position_fee_fills_select(position: Position, *, product_id: str) -> Select[Any]:
    """Select bounded applied fills since entry for one product's remaining entry fees."""
    return (
        select(execution_fills, execution_orders.c.side)
        .join(execution_orders, execution_fills.c.order_id == execution_orders.c.id)
        .join(deployments, execution_fills.c.deployment_id == deployments.c.id)
        .where(
            execution_fills.c.deployment_id == position.deployment_id,
            execution_orders.c.deployment_id == position.deployment_id,
            execution_fills.c.filled_at >= position.entered_bar,
            execution_fills.c.economics_applied_at.is_not(None),
            func.coalesce(func.nullif(execution_orders.c.product_id, ""), deployments.c.product_id)
            == product_id,
        )
        .order_by(execution_fills.c.filled_at.asc(), execution_fills.c.venue_fill_id.asc())
        .limit(MAX_POSITION_FEE_FILLS + 1)
    )


def _fills_page_select(deployment_id: UUID, *, limit: int, cursor: str | None) -> Select[Any]:
    """Select one descending fill page (plus one look-ahead row) after the cursor."""
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
    return statement


def _orders_page_select(deployment_id: UUID, *, limit: int, cursor: str | None) -> Select[Any]:
    """Select one descending order page (plus one look-ahead row) after the cursor."""
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
    return statement


def _breaker_pause_update(
    deployment_id: UUID, *, expected_revision: int, detail: str, daily_loss_latched: bool
) -> ReturningUpdate[Any]:
    """CAS only breaker-owned metadata; economics, lifecycle and runtime rows are untouched."""
    running = deployments.c.status == DeploymentStatus.RUNNING.value
    return (
        deployments.update()
        .where(deployments.c.id == deployment_id, deployments.c.revision == expected_revision)
        .values(
            status=case((running, DeploymentStatus.PAUSED.value), else_=deployments.c.status),
            mismatch_detail=case((running, detail), else_=deployments.c.mismatch_detail),
            daily_loss_latched=True if daily_loss_latched else deployments.c.daily_loss_latched,
            updated_at=utc_now(),
            revision=deployments.c.revision + 1,
        )
        .returning(deployments)
    )


def _worker_lease_update(
    deployment_id: UUID, *, holder: str, now: datetime, ttl: timedelta
) -> ReturningUpdate[Any]:
    """Acquire or renew a fenced worker lease when it is free, expired, or already held."""
    expires_at = now + ttl
    return (
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


def _intent_insert(intent: OrderIntent) -> Insert:
    """Insert one order intent before venue submission."""
    return insert(order_intents).values(
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


def _order_upsert(order: Order) -> Insert:
    """Insert or replace one venue-visible order snapshot by client order id."""
    values = _order_values(order)
    statement = insert(execution_orders).values(values)
    return statement.on_conflict_do_update(
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


def _applied_order_upsert(order_values: dict[str, object]) -> Insert:
    """Upsert an order's fill progress after its fill economics are applied."""
    return (
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


def _fill_insert(fill: Fill, *, economics_applied_at: datetime | None) -> Insert:
    """Insert one fill, ignoring exact venue-fill duplicates."""
    return (
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
            economics_applied_at=economics_applied_at,
        )
        .on_conflict_do_nothing(constraint="ux_execution_fills_venue")
    )


def _position_insert(position: Position, *, stamped_product: str) -> Insert:
    """Insert one product book's position row."""
    return insert(execution_positions).values(
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
