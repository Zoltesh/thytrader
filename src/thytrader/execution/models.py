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


class DeploymentKind(StrEnum):
    """Whether a runtime is a published strategy or a discretionary book."""

    STRATEGY = "strategy"
    DISCRETIONARY = "discretionary"


class IntentOrigin(StrEnum):
    """Who requested the intent so audits can separate human, agent, and runtime."""

    HUMAN = "human"
    AGENT = "agent"
    RUNTIME = "runtime"


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


class PositionSide(StrEnum):
    """Spot inventory direction for one deployment."""

    LONG = "long"
    SHORT = "short"


class OrderKind(StrEnum):
    """Maker, marketable, or venue-native OCO execution style used by the runtime."""

    POST_ONLY_LIMIT = "post_only_limit"
    MARKETABLE = "marketable"
    TRIGGER_BRACKET = "trigger_bracket"


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
    BRACKET = "bracket"


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
    stop_trigger_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    status: OrderStatus = OrderStatus.PENDING
    origin: IntentOrigin = IntentOrigin.RUNTIME
    idempotency_key: str | None = None
    product_id: str = ""


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
    stop_trigger_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    product_id: str = ""


@dataclass(frozen=True, slots=True)
class Fill:
    """One exact fill against a known order.

    ``applied_at`` is set only after the fill's cash/inventory/order effect has
    been committed in the same transaction as the fill row (see
    ``ExecutionStore.apply_fill_effect``). Presence of a fill row is evidence,
    not proof the economic effect landed; reconciliation must not treat
    unapplied rows as coverage.
    """

    id: UUID
    deployment_id: UUID
    order_id: UUID
    venue_fill_id: str
    price: Decimal
    quantity: Decimal
    fee: Decimal
    filled_at: datetime
    venue_order_id: str | None = None
    applied_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Position:
    """One long or short product book held by a deployment."""

    deployment_id: UUID
    quantity: Decimal
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    entered_bar: datetime
    updated_at: datetime
    trail_extreme: Decimal | None = None
    side: PositionSide = PositionSide.LONG
    product_id: str = ""
    add_count: int = 1
    last_fill_intent_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Deployment:
    """One paper or live runtime bound to a published strategy or a discretionary book."""

    id: UUID
    strategy_fingerprint: str | None
    strategy_id: UUID | None
    product_id: str
    mode: DeploymentMode
    status: DeploymentStatus
    cash: Decimal
    phase: RuntimePhase
    created_at: datetime
    updated_at: datetime
    paper_starting_cash: Decimal | None = None
    paper_maker_fee_rate: Decimal | None = None
    paper_taker_fee_rate: Decimal | None = None
    last_evaluated_bar: datetime | None = None
    last_signal: str | None = None
    mismatch_detail: str | None = None
    pending_entry_bars: int = 0
    bars_held: int = 0
    cooldown_bars_remaining: int = 0
    pending_stop_price: Decimal | None = None
    pending_target_price: Decimal | None = None
    kind: DeploymentKind = DeploymentKind.STRATEGY
    timeframe: str | None = None


@dataclass(frozen=True, slots=True)
class InstrumentRuntime:
    """Per-product overlay of the single-book runtime machine."""

    product_id: str
    phase: RuntimePhase
    last_evaluated_bar: datetime | None = None
    last_signal: str | None = None
    pending_entry_bars: int = 0
    bars_held: int = 0
    cooldown_bars_remaining: int = 0
    pending_stop_price: Decimal | None = None
    pending_target_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class DeploymentSnapshot:
    """One deployment plus positions, orders, fills, and per-product runtime overlays."""

    deployment: Deployment
    position: Position | None = None
    orders: tuple[Order, ...] = field(default_factory=tuple)
    fills: tuple[Fill, ...] = field(default_factory=tuple)
    intents: tuple[OrderIntent, ...] = field(default_factory=tuple)
    positions: tuple[Position, ...] = field(default_factory=tuple)
    instrument_runtimes: tuple[InstrumentRuntime, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class FillApplication:
    """Order, deployment, and position state to persist atomically with one fill.

    ``clear_position`` distinguishes "no book remains open" from "unchanged"; when
    true the store deletes this fill's product position row instead of leaving a
    stale one in place. ``position`` is ignored when ``clear_position`` is true.
    """

    fill: Fill
    order: Order
    deployment: Deployment
    position: Position | None
    clear_position: bool


@dataclass(frozen=True, slots=True)
class FillApplicationResult:
    """Outcome of one atomic, idempotent fill-application attempt.

    ``applied`` is false when this exact venue fill was already applied by an
    earlier call (crash-safe restart, retried reconciliation, or a duplicate
    submit-completion path). Callers must not repeat any economic side effect in
    that case and should only refresh their snapshot from durable storage.
    """

    applied: bool


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


def snapshot_positions(snapshot: DeploymentSnapshot) -> tuple[Position, ...]:
    """Return every product book, falling back to the focused position for older snapshots."""
    if snapshot.positions:
        return snapshot.positions
    if snapshot.position is not None:
        return (snapshot.position,)
    return ()


def resolved_product_id(product_id: str, deployment: Deployment) -> str:
    """Treat a blank product id as the deployment's primary Coinbase product."""
    return product_id or deployment.product_id


def runtime_from_deployment(deployment: Deployment, product_id: str) -> InstrumentRuntime:
    """Project deployment-row runtime fields onto one product overlay."""
    return InstrumentRuntime(
        product_id=product_id,
        phase=deployment.phase,
        last_evaluated_bar=deployment.last_evaluated_bar,
        last_signal=deployment.last_signal,
        pending_entry_bars=deployment.pending_entry_bars,
        bars_held=deployment.bars_held,
        cooldown_bars_remaining=deployment.cooldown_bars_remaining,
        pending_stop_price=deployment.pending_stop_price,
        pending_target_price=deployment.pending_target_price,
    )


def aggregate_phase(runtimes: tuple[InstrumentRuntime, ...]) -> RuntimePhase:
    """Collapse per-product phases for operator-visible deployment status."""
    phases = {item.phase for item in runtimes}
    if RuntimePhase.OPEN in phases:
        return RuntimePhase.OPEN
    if RuntimePhase.PENDING_EXIT in phases:
        return RuntimePhase.PENDING_EXIT
    if RuntimePhase.PENDING_ENTRY in phases:
        return RuntimePhase.PENDING_ENTRY
    return RuntimePhase.FLAT
