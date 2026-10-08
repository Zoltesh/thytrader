"""Render deployment snapshots and summaries into deployment HTTP response models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.api.routes.deployment_models import (
    DeploymentBookTotalsResponse,
    DeploymentCapitalResponse,
    DeploymentLedgerSummaryResponse,
    DeploymentResponse,
    FillResponse,
    InstrumentRuntimeResponse,
    OrderResponse,
    PositionResponse,
)
from thytrader.execution.service import resolved_deployment_timeframe
from thytrader.fleet_control.models import SUMMARY_LEDGER_OMISSION
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.models import (
    RuntimePhase,
    resolved_product_id,
    snapshot_positions,
    summary_as_snapshot,
    visible_instrument_runtimes,
)
from thytrader.trading.protection import (
    PositionState,
    book_exit_in_flight,
    book_position_state,
    book_protection_evidence,
    deployment_position_state,
    protection_evidence_response,
    working_order_count,
)

if TYPE_CHECKING:
    from decimal import Decimal
    from uuid import UUID

    from thytrader.strategies.snapshots import StrategySnapshotStore
    from thytrader.trading.ledger import DeploymentLedger
    from thytrader.trading.models import (
        Deployment,
        DeploymentSnapshot,
        DeploymentSummarySnapshot,
        Fill,
        InstrumentRuntime,
        Order,
        Position,
    )


def _optional_decimal_string(value: Decimal | None) -> str | None:
    """Format one optional Decimal for JSON without inventing zero placeholders."""
    if value is None:
        return None
    return format(value, "f")


def _capital_response(deployment: Deployment) -> DeploymentCapitalResponse:
    """Serialize live/paper capital accounting separate from ledger cash."""
    return DeploymentCapitalResponse(
        allocated_capital=_optional_decimal_string(deployment.allocated_capital),
        venue_available_quote=_optional_decimal_string(deployment.venue_available_quote),
        reserved_buying_power=_optional_decimal_string(deployment.reserved_buying_power),
        inventory_cost=_optional_decimal_string(deployment.inventory_cost),
        performance_equity=_optional_decimal_string(deployment.performance_equity),
        performance_capital_quote=_optional_decimal_string(deployment.performance_capital_quote),
        performance_maximum_drawdown_fraction=_optional_decimal_string(
            deployment.performance_maximum_drawdown_fraction
        ),
        initial_equity=_optional_decimal_string(deployment.initial_equity),
        baseline_equity=_optional_decimal_string(deployment.baseline_equity),
        high_water_mark_equity=_optional_decimal_string(deployment.high_water_mark_equity),
        utc_day_open_equity=_optional_decimal_string(deployment.utc_day_open_equity),
        risk_day_open_evidence=deployment.risk_day_open_evidence,
    )


def _deployment_response(
    deployment: Deployment,
    *,
    timeframe: str | None,
) -> DeploymentResponse:
    """Serialize one deployment without related collections."""
    return DeploymentResponse(
        id=deployment.id,
        strategy_fingerprint=deployment.strategy_fingerprint,
        strategy_id=deployment.strategy_id,
        strategy_name=deployment.strategy_name,
        strategy_deleted=deployment.strategy_deleted,
        portfolio_id=deployment.portfolio_id,
        kind=deployment.kind.value,
        timeframe=timeframe,
        product_id=deployment.product_id,
        mode=deployment.mode.value,
        status=deployment.status.value,
        phase=deployment.phase.value,
        cash=format(deployment.cash, "f"),
        paper_starting_cash=(
            None
            if deployment.paper_starting_cash is None
            else format(deployment.paper_starting_cash, "f")
        ),
        maker_fee_rate=(
            None
            if deployment.paper_maker_fee_rate is None
            else format(deployment.paper_maker_fee_rate, "f")
        ),
        taker_fee_rate=(
            None
            if deployment.paper_taker_fee_rate is None
            else format(deployment.paper_taker_fee_rate, "f")
        ),
        last_evaluated_bar=(
            None
            if deployment.last_evaluated_bar is None
            else deployment.last_evaluated_bar.isoformat()
        ),
        last_signal=deployment.last_signal,
        mismatch_detail=deployment.mismatch_detail,
        pending_entry_bars=deployment.pending_entry_bars,
        bars_held=deployment.bars_held,
        lifecycle_command=deployment.lifecycle_command.value,
        daily_loss_latched=deployment.daily_loss_latched,
        drawdown_latched=deployment.drawdown_latched,
        revision=deployment.revision,
        worker_lease_held=_worker_lease_held(deployment),
        created_at=deployment.created_at.isoformat(),
        updated_at=deployment.updated_at.isoformat(),
        capital=_capital_response(deployment),
    )


def _worker_lease_held(deployment: Deployment) -> bool:
    """True when a worker lease is active without exposing holder identity."""
    return bool(deployment.worker_lease_holder and deployment.worker_lease_expires_at)


async def snapshot_response(
    snapshot: DeploymentSnapshot,
    publication_store: StrategySnapshotStore | None = None,
    *,
    extra_product_ids: tuple[str, ...] = (),
) -> DeploymentResponse:
    """Serialize one deployment together with every product book, orders, and fills."""
    if publication_store is None:
        timeframe = snapshot.deployment.timeframe
    else:
        timeframe = await resolved_deployment_timeframe(snapshot.deployment, publication_store)
    response = _deployment_response(snapshot.deployment, timeframe=timeframe)
    positions = _position_collection(snapshot)
    order_products = _order_product_ids(snapshot)
    ledger = ledger_from_snapshot(snapshot)
    state = deployment_position_state(snapshot)
    return response.model_copy(
        update={
            "position_state": state.value,
            "exit_in_flight": state is PositionState.EXITING,
            "position": _compatibility_position(snapshot, positions),
            "positions": positions,
            "instrument_runtimes": _runtime_collection(
                snapshot, extra_product_ids=extra_product_ids
            ),
            "book_totals": DeploymentBookTotalsResponse(
                open_books=len(positions),
                working_orders=working_order_count(snapshot.orders),
                fill_count=len(snapshot.fills),
            ),
            "detail": "full",
            "historical_orders_included": True,
            "historical_fills_included": True,
            "ledger_omission": None,
            "capital": _accounting_capital_response(response.capital, ledger),
            "ledger": ledger_summary_response(ledger),
            "orders": tuple(
                order_response(order, product_id=order_products[order.id])
                for order in snapshot.orders
            ),
            "fills": tuple(
                fill_response(
                    fill,
                    product_id=order_products.get(fill.order_id, snapshot.deployment.product_id),
                )
                for fill in snapshot.fills
            ),
        }
    )


async def summary_response(
    summary: DeploymentSummarySnapshot,
    publication_store: StrategySnapshotStore | None = None,
    *,
    extra_product_ids: tuple[str, ...] = (),
) -> DeploymentResponse:
    """Serialize one deployment summary without historical orders or fills."""
    snapshot = summary_as_snapshot(summary)
    if publication_store is None:
        timeframe = summary.deployment.timeframe
    else:
        timeframe = await resolved_deployment_timeframe(summary.deployment, publication_store)
    response = _deployment_response(summary.deployment, timeframe=timeframe)
    positions = _position_collection(snapshot)
    ledger = ledger_from_snapshot(snapshot)
    state = deployment_position_state(snapshot)
    return response.model_copy(
        update={
            "position_state": state.value,
            "exit_in_flight": state is PositionState.EXITING,
            "position": _compatibility_position(snapshot, positions),
            "positions": positions,
            "instrument_runtimes": _runtime_collection(
                snapshot, extra_product_ids=extra_product_ids
            ),
            "book_totals": DeploymentBookTotalsResponse(
                open_books=summary.book_totals.open_books,
                working_orders=summary.book_totals.working_orders,
                fill_count=summary.book_totals.fill_count,
            ),
            "detail": "summary",
            "historical_orders_included": False,
            "historical_fills_included": False,
            "ledger_omission": SUMMARY_LEDGER_OMISSION,
            "capital": _accounting_capital_response(response.capital, ledger),
            "ledger": ledger_summary_response(ledger),
            "orders": (),
            "fills": (),
        }
    )


def _accounting_capital_response(
    capital: DeploymentCapitalResponse, ledger: DeploymentLedger
) -> DeploymentCapitalResponse:
    """Retain independent funding/budget history, not stale current totals as complete facts."""
    if ledger.accounting_complete:
        return capital
    return capital.model_copy(
        update={"inventory_cost": None, "reserved_buying_power": None, "performance_equity": None}
    )


def ledger_summary_response(ledger: DeploymentLedger) -> DeploymentLedgerSummaryResponse:
    """Render aggregate ledger statistics for bounded deployment reads."""
    return DeploymentLedgerSummaryResponse(
        trade_count=ledger.trade_count,
        total_net_pnl=ledger.total_net_pnl_text(),
        total_return_fraction=ledger.total_return_fraction_text(),
        mark_complete=ledger.mark_complete,
        marked_exposure=(
            None if ledger.marked_exposure is None else format(ledger.marked_exposure, "f")
        ),
    )


def _position_collection(snapshot: DeploymentSnapshot) -> tuple[PositionResponse, ...]:
    """Serialize every open product book with protection status, sorted by product id."""
    responses = [
        position_response(item, snapshot, compatibility_focus=False)
        for item in snapshot_positions(snapshot)
    ]
    return tuple(sorted(responses, key=lambda item: item.product_id))


def _runtime_collection(
    snapshot: DeploymentSnapshot, *, extra_product_ids: tuple[str, ...]
) -> tuple[InstrumentRuntimeResponse, ...]:
    """Serialize overlay rows for every known and published product id."""
    return tuple(
        _runtime_response(item)
        for item in visible_instrument_runtimes(snapshot, extra_product_ids=extra_product_ids)
    )


def _compatibility_position(
    snapshot: DeploymentSnapshot, positions: tuple[PositionResponse, ...]
) -> PositionResponse | None:
    """Label the store's focused book as compatibility-only inventory."""
    focused = snapshot.position
    if focused is None:
        return None
    product_id = resolved_product_id(focused.product_id, snapshot.deployment)
    for item in positions:
        if item.product_id == product_id:
            return item.model_copy(update={"compatibility_focus": True})
    return position_response(focused, snapshot, compatibility_focus=True)


def position_response(
    position: Position,
    snapshot: DeploymentSnapshot,
    *,
    compatibility_focus: bool,
) -> PositionResponse:
    """Serialize one open long or short product book."""
    product_id = resolved_product_id(position.product_id, snapshot.deployment)
    evidence = book_protection_evidence(snapshot, product_id=product_id, position=position)
    return PositionResponse(
        product_id=product_id,
        quantity=format(position.quantity, "f"),
        entry_price=format(position.entry_price, "f"),
        stop_price=format(position.stop_price, "f"),
        target_price=_optional_decimal(position.target_price),
        entered_bar=position.entered_bar.isoformat(),
        side=position.side.value,
        trail_extreme=(
            None if position.trail_extreme is None else format(position.trail_extreme, "f")
        ),
        add_count=position.add_count,
        signal_exit_bar=(
            None if position.signal_exit_bar is None else position.signal_exit_bar.isoformat()
        ),
        protection_status=evidence.status.value,
        protection=protection_evidence_response(evidence),
        position_state=book_position_state(
            snapshot,
            product_id=product_id,
            position=position,
            phase=RuntimePhase.OPEN,
            evidence=evidence,
        ).value,
        exit_in_flight=book_exit_in_flight(snapshot, product_id=product_id, position=position),
        compatibility_focus=compatibility_focus,
    )


def _runtime_response(runtime: InstrumentRuntime) -> InstrumentRuntimeResponse:
    """Serialize one per-product overlay."""
    return InstrumentRuntimeResponse(
        product_id=runtime.product_id,
        phase=runtime.phase.value,
        last_evaluated_bar=(
            None if runtime.last_evaluated_bar is None else runtime.last_evaluated_bar.isoformat()
        ),
        last_signal=runtime.last_signal,
        pending_entry_bars=runtime.pending_entry_bars,
        bars_held=runtime.bars_held,
        cooldown_bars_remaining=runtime.cooldown_bars_remaining,
        pending_stop_price=_optional_decimal(runtime.pending_stop_price),
        pending_target_price=_optional_decimal(runtime.pending_target_price),
    )


def _order_product_ids(snapshot: DeploymentSnapshot) -> dict[UUID, str]:
    """Map each order onto its Coinbase product, treating blank ids as primary."""
    return {
        order.id: resolved_product_id(order.product_id, snapshot.deployment)
        for order in snapshot.orders
    }


def order_response(order: Order, *, product_id: str) -> OrderResponse:
    """Serialize one order snapshot with its product identity."""
    return OrderResponse(
        id=order.id,
        client_order_id=order.client_order_id,
        venue_order_id=order.venue_order_id,
        product_id=product_id,
        side=order.side.value,
        kind=order.kind.value,
        quantity=format(order.quantity, "f"),
        price=None if order.price is None else format(order.price, "f"),
        stop_trigger_price=(
            None if order.stop_trigger_price is None else format(order.stop_trigger_price, "f")
        ),
        take_profit_price=(
            None if order.take_profit_price is None else format(order.take_profit_price, "f")
        ),
        filled_quantity=format(order.filled_quantity, "f"),
        status=order.status.value,
        reject_reason=order.reject_reason,
        created_at=order.created_at.isoformat(),
        updated_at=order.updated_at.isoformat(),
        attached_child_venue_order_id=order.attached_child_venue_order_id,
        parent_order_id=order.parent_order_id,
        pyramid_add=order.pyramid_add,
    )


def fill_response(fill: Fill, *, product_id: str) -> FillResponse:
    """Serialize one fill with the parent order's product identity."""
    return FillResponse(
        id=fill.id,
        order_id=fill.order_id,
        product_id=product_id,
        venue_fill_id=fill.venue_fill_id,
        price=format(fill.price, "f"),
        quantity=format(fill.quantity, "f"),
        fee=format(fill.fee, "f"),
        filled_at=fill.filled_at.isoformat(),
    )


def _optional_decimal(value: Decimal | None) -> str | None:
    """Format an optional Decimal the same way as other deployment JSON fields."""
    if value is None:
        return None
    return format(value, "f")
