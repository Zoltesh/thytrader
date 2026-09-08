"""Typed execution records shared by paper and live runtimes."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime  # noqa: TC003 - dataclass fields resolve at type-check and runtime.
from decimal import Decimal
from enum import StrEnum
from uuid import UUID  # noqa: TC003 - dataclass fields resolve at type-check and runtime.


class DeploymentMode(StrEnum):
    """Venue selected when a published strategy is deployed."""

    PAPER = "paper"
    LIVE = "live"


class DeploymentStatus(StrEnum):
    """Operator-visible lifecycle of one deployment."""

    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class RuntimePhase(StrEnum):
    """Single-position runtime state machine."""

    FLAT = "flat"
    PENDING_ENTRY = "pending_entry"
    OPEN = "open"
    PENDING_EXIT = "pending_exit"


class OrderSide(StrEnum):
    """Spot order side."""

    BUY = "buy"
    SELL = "sell"


class OrderKind(StrEnum):
    """Maker-or-marketable execution style used by the runtime."""

    POST_ONLY_LIMIT = "post_only_limit"
    MARKETABLE = "marketable"


class OrderStatus(StrEnum):
    """Persisted venue-neutral order status."""

    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class IntentPurpose(StrEnum):
    """Why the runtime created one order intent."""

    ENTRY = "entry"
    TAKE_PROFIT = "take_profit"
    STOP = "stop"
    TIME_EXIT = "time_exit"


class ExecutionStoreError(RuntimeError):
    """Signal that durable execution storage is disabled or unavailable."""


class ExecutionConflictError(ValueError):
    """Reject a deployment mutation that violates a runtime invariant."""


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """One persisted intent recorded before any venue submission."""

    id: UUID
    deployment_id: UUID
    client_order_id: str
    purpose: IntentPurpose
    side: OrderSide
    kind: OrderKind
    quantity: Decimal
    created_at: datetime
    candle_starts_at: datetime
    price: Decimal | None = None
    status: OrderStatus = OrderStatus.PENDING


@dataclass(frozen=True, slots=True)
class Order:
    """One venue-visible order derived from a persisted intent."""

    id: UUID
    deployment_id: UUID
    intent_id: UUID
    client_order_id: str
    side: OrderSide
    kind: OrderKind
    quantity: Decimal
    status: OrderStatus
    created_at: datetime
    updated_at: datetime
    price: Decimal | None = None
    filled_quantity: Decimal = Decimal("0")
    venue_order_id: str | None = None
    reject_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Fill:
    """One exact fill against a known order."""

    id: UUID
    deployment_id: UUID
    order_id: UUID
    venue_fill_id: str
    price: Decimal
    quantity: Decimal
    fee: Decimal
    filled_at: datetime
    venue_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class Position:
    """The single long position held by one deployment, if any."""

    deployment_id: UUID
    quantity: Decimal
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    entered_bar: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Deployment:
    """One paper or live runtime bound to an immutable published strategy."""

    id: UUID
    strategy_fingerprint: str
    strategy_id: UUID
    product_id: str
    mode: DeploymentMode
    status: DeploymentStatus
    cash: Decimal
    phase: RuntimePhase
    created_at: datetime
    updated_at: datetime
    paper_starting_cash: Decimal | None = None
    last_evaluated_bar: datetime | None = None
    last_signal: str | None = None
    mismatch_detail: str | None = None
    pending_entry_bars: int = 0
    bars_held: int = 0
    cooldown_bars_remaining: int = 0
    pending_stop_price: Decimal | None = None
    pending_target_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class DeploymentSnapshot:
    """One deployment plus its current position, orders, and fills."""

    deployment: Deployment
    position: Position | None = None
    orders: tuple[Order, ...] = field(default_factory=tuple)
    fills: tuple[Fill, ...] = field(default_factory=tuple)
    intents: tuple[OrderIntent, ...] = field(default_factory=tuple)


def with_status(
    deployment: Deployment, status: DeploymentStatus, updated_at: datetime
) -> Deployment:
    """Return a copy with an updated operator status."""
    return replace(deployment, status=status, updated_at=updated_at)


def with_runtime(
    deployment: Deployment,
    *,
    updated_at: datetime,
    cash: Decimal | None = None,
    phase: RuntimePhase | None = None,
    last_evaluated_bar: datetime | None = None,
    last_signal: str | None = None,
    mismatch_detail: str | None = None,
    clear_mismatch: bool = False,
    pending_entry_bars: int | None = None,
    bars_held: int | None = None,
    cooldown_bars_remaining: int | None = None,
    pending_stop_price: Decimal | None = None,
    pending_target_price: Decimal | None = None,
    clear_pending_levels: bool = False,
    status: DeploymentStatus | None = None,
) -> Deployment:
    """Return a copy with updated runtime fields, leaving omitted values unchanged."""
    stop_price = (
        None
        if clear_pending_levels
        else (deployment.pending_stop_price if pending_stop_price is None else pending_stop_price)
    )
    target_price = (
        None
        if clear_pending_levels
        else (
            deployment.pending_target_price
            if pending_target_price is None
            else pending_target_price
        )
    )
    detail = (
        None
        if clear_mismatch
        else (deployment.mismatch_detail if mismatch_detail is None else mismatch_detail)
    )
    return replace(
        deployment,
        cash=deployment.cash if cash is None else cash,
        phase=deployment.phase if phase is None else phase,
        last_evaluated_bar=(
            deployment.last_evaluated_bar if last_evaluated_bar is None else last_evaluated_bar
        ),
        last_signal=deployment.last_signal if last_signal is None else last_signal,
        mismatch_detail=detail,
        pending_entry_bars=(
            deployment.pending_entry_bars if pending_entry_bars is None else pending_entry_bars
        ),
        bars_held=deployment.bars_held if bars_held is None else bars_held,
        cooldown_bars_remaining=(
            deployment.cooldown_bars_remaining
            if cooldown_bars_remaining is None
            else cooldown_bars_remaining
        ),
        pending_stop_price=stop_price,
        pending_target_price=target_price,
        status=deployment.status if status is None else status,
        updated_at=updated_at,
    )
