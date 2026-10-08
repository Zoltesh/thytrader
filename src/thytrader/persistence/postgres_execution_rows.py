"""Row mappers and column-value builders for the PostgreSQL execution store.

Pure translation between execution domain models and ``deployments``, ``order_intents``,
``execution_orders``, ``execution_fills``, ``execution_positions``,
``execution_instrument_state`` and ``deployment_twin_links`` rows (exact decimal strings),
plus the product-state write statements shared by ordinary saves and atomic fill commits.
Used by :mod:`thytrader.persistence.postgres_execution` and
:mod:`thytrader.persistence.postgres_execution_snapshots`; imports neither.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import exists
from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.orm import aliased

from thytrader.persistence.schema import execution_instrument_state
from thytrader.trading.day_open import DailyOpeningEvidence
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
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
    Position,
    PositionSide,
    RuntimePhase,
    runtime_from_deployment,
)
from thytrader.trading.twins import DeploymentTwinLink

if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.sql.dml import Update


def _primary_runtime_mirror(deployment: Deployment) -> Update:
    """Refresh a single-product book's existing overlay row from its deployment row.

    The deployment row owns a single-product book's runtime; a lagging mirror would make
    product-scoped readers replay an evaluated bar. Multi-product overlays are untouched,
    and no row is created.
    """
    runtime = runtime_from_deployment(deployment, deployment.product_id)
    state = execution_instrument_state
    other = aliased(execution_instrument_state)
    return (
        state.update()
        .where(
            state.c.deployment_id == deployment.id,
            state.c.product_id == deployment.product_id,
            ~exists().where(
                other.c.deployment_id == deployment.id,
                other.c.product_id != deployment.product_id,
            ),
        )
        .values(
            phase=runtime.phase.value,
            last_evaluated_bar=runtime.last_evaluated_bar,
            last_signal=runtime.last_signal,
            pending_entry_bars=runtime.pending_entry_bars,
            bars_held=runtime.bars_held,
            cooldown_bars_remaining=runtime.cooldown_bars_remaining,
            pending_stop_price=_text(runtime.pending_stop_price),
            pending_target_price=_text(runtime.pending_target_price),
        )
    )


def _instrument_runtime_upsert(runtime: InstrumentRuntime, deployment_id: UUID) -> Insert:
    """Build the same product-state write for ordinary saves and atomic fill commits."""
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
    return statement.on_conflict_do_update(
        index_elements=[
            execution_instrument_state.c.deployment_id,
            execution_instrument_state.c.product_id,
        ],
        set_={
            name: getattr(statement.excluded, name)
            for name in values
            if name not in {"deployment_id", "product_id"}
        },
    )


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
        "risk_day_open_evidence": (
            None
            if deployment.risk_day_open_evidence is None
            else deployment.risk_day_open_evidence.model_dump_json()
        ),
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
        risk_day_open_evidence=(
            None
            if row.get("risk_day_open_evidence") is None
            else DailyOpeningEvidence.model_validate_json(str(row["risk_day_open_evidence"]))
        ),
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


def _twin_link_from_row(row: RowMapping) -> DeploymentTwinLink:
    """Restore one explicit pair from its durable row."""
    return DeploymentTwinLink(
        row["paper_deployment_id"], row["live_deployment_id"], row["linked_at"]
    )
