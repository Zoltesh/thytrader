"""Paper and live deployment HTTP contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID  # noqa: TC003 - FastAPI resolves this annotation at runtime.

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, StrictBool

from thytrader.api.dependencies import (
    get_audit_event_store,
    get_execution_store,
    get_market_data_watchlist_store,
    get_optional_decision_journal_store,
    get_risk_policy_store,
    get_runtime_state,
    get_strategy_snapshot_store,
    get_strategy_store,
)
from thytrader.api.live_ack import require_live_acknowledgement
from thytrader.api.strategy_http import snapshot_for_start
from thytrader.data_control.service import ingestion_provider
from thytrader.execution.book_marks import last_bar_marks, signed_unrealized_pnl
from thytrader.execution.decision_store import (
    DecisionJournalStore,  # noqa: TC001 - FastAPI Depends.
)
from thytrader.execution.ledger import DeploymentLedger, ledger_from_snapshot
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    DeploymentSummarySnapshot,
    ExecutionConflictError,
    ExecutionStoreError,
    Fill,
    InstrumentRuntime,
    Order,
    Position,
    PositionSide,
    RuntimePhase,
    resolved_product_id,
    snapshot_positions,
    visible_instrument_runtimes,
)
from thytrader.execution.protection import (
    PositionState,
    book_exit_in_flight,
    book_position_state,
    book_protection_status,
    deployment_position_state,
    working_order_count,
)
from thytrader.execution.service import (
    ReferenceWatchlist,
    create_deployment,
    parse_decimal,
    reset_breaker_latches,
    resolved_deployment_timeframe,
    set_deployment_status,
)
from thytrader.execution.store import ExecutionStore  # noqa: TC001 - FastAPI Depends.
from thytrader.market_data.watchlist import (
    MarketDataWatchlistStore,  # noqa: TC001 - FastAPI Depends.
)
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)
from thytrader.risk.store import RiskPolicyStore  # noqa: TC001 - FastAPI Depends.
from thytrader.runtime import RuntimeState  # noqa: TC001 - FastAPI Depends.
from thytrader.strategies.library import StrategyStore  # noqa: TC001 - FastAPI Depends.
from thytrader.strategies.models import covered_product_ids
from thytrader.strategies.snapshots import (
    StrategySnapshotError,
    StrategySnapshotStore,
)

if TYPE_CHECKING:
    from thytrader.execution.book_marks import BookMark

router = APIRouter(prefix="/api/v1/deployments", tags=["deployments"])


class CreateDeploymentRequest(BaseModel):
    """Start one paper or live runtime from a strategy's current (valid) rules.

    The server snapshots the definition; the response's ``strategy_fingerprint``
    names the exact rules the bot runs, even after later edits.
    """

    strategy_id: UUID
    mode: DeploymentMode
    paper_starting_cash: str | None = None
    maker_fee_rate: str | None = None
    taker_fee_rate: str | None = None
    i_understand_live: StrictBool = Field(
        default=False,
        description=(
            "Required true for mode=live (HTTP 428 live_acknowledgement_required otherwise). "
            "Send only after the operator explicitly acknowledged live trading."
        ),
    )


class ResumeDeploymentRequest(BaseModel):
    """Optional resume body; live books require the explicit live acknowledgement."""

    i_understand_live: StrictBool = Field(
        default=False,
        description="Required true to resume a live deployment (re-arms live order submission).",
    )


class PositionResponse(BaseModel):
    """One long or short product book, including protection status."""

    product_id: str
    quantity: str
    entry_price: str
    stop_price: str
    target_price: str | None = Field(
        default=None, description="Take-profit price; null when the strategy declares none."
    )
    entered_bar: str
    side: str = "long"
    trail_extreme: str | None = None
    add_count: int = 1
    signal_exit_bar: str | None = Field(
        default=None,
        description=(
            "UTC start of the closed bar whose exits.signal_exit rule matched; the book is "
            "exiting (ADR 0093). Null when no signal exit is pending."
        ),
    )
    protection_status: str
    position_state: str = Field(
        default="open_protected",
        description=(
            "Operator reading of this book (ADR 0097): open_protected (TP/SL bracket, "
            "stop-only protection, or the paper synthetic stop rests), open_unprotected, "
            "open_unverified, or exiting. Prefer it over the raw phase, which reads "
            "pending_exit while protection merely rests."
        ),
    )
    exit_in_flight: bool = Field(
        default=False,
        description=(
            "True only when this book's exit is being sent: a working marketable exit, a "
            "matched signal exit, or a flatten. A resting TP/SL bracket is not an exit."
        ),
    )
    mark_price: str | None = Field(
        default=None,
        description=(
            "Close of the newest bar the bot evaluated for this product (ADR 0098); null "
            "when no journaled close exists or on reads that do not mark books."
        ),
    )
    marked_at: str | None = Field(
        default=None, description="UTC close time of the bar behind mark_price."
    )
    unrealized_pnl: str | None = Field(
        default=None,
        description=(
            "Gross unrealized PnL at mark_price in quote currency (signed quantity times "
            "the move from entry_price), before exit fees; null without a mark."
        ),
    )
    compatibility_focus: bool = False


class InstrumentRuntimeResponse(BaseModel):
    """Per-product overlay of the single-book runtime machine."""

    product_id: str
    phase: str
    last_evaluated_bar: str | None
    last_signal: str | None
    pending_entry_bars: int
    bars_held: int
    cooldown_bars_remaining: int
    pending_stop_price: str | None = None
    pending_target_price: str | None = None


class DeploymentBookTotalsResponse(BaseModel):
    """Collection counts that must match `positions`, working orders, and fills."""

    open_books: int = 0
    working_orders: int = 0
    fill_count: int = 0


class DeploymentCapitalResponse(BaseModel):
    """Capital accounting separate from ledger ``cash``.

    Ledger ``cash`` on the parent body is fill accounting. Live sizing and breaker
    fractions use allocated capital or venue available quote. Unknown venue quote is
    ``null`` so callers disable entries instead of reusing a stale balance.
    """

    allocated_capital: str | None = None
    venue_available_quote: str | None = None
    reserved_buying_power: str | None = None
    inventory_cost: str | None = None
    performance_equity: str | None = None
    initial_equity: str | None = None
    baseline_equity: str | None = None
    high_water_mark_equity: str | None = None
    utc_day_open_equity: str | None = None


class OrderResponse(BaseModel):
    """One persisted venue-visible order, tagged with its Coinbase product."""

    id: UUID
    client_order_id: str
    venue_order_id: str | None
    product_id: str
    side: str
    kind: str
    quantity: str
    price: str | None
    stop_trigger_price: str | None = None
    take_profit_price: str | None = None
    filled_quantity: str
    status: str
    reject_reason: str | None
    created_at: str
    updated_at: str
    attached_child_venue_order_id: str | None = None
    parent_order_id: UUID | None = None
    pyramid_add: bool = False


class FillResponse(BaseModel):
    """One persisted fill, tagged with the parent order's product."""

    id: UUID
    order_id: UUID
    product_id: str
    venue_fill_id: str
    price: str
    quantity: str
    fee: str
    filled_at: str


class DeploymentLedgerSummaryResponse(BaseModel):
    """Aggregate fill-ledger statistics without loading every historical fill."""

    trade_count: int
    total_net_pnl: str | None = None
    total_return_fraction: str | None = None
    mark_complete: bool
    marked_exposure: str | None = None


class DeploymentResponse(BaseModel):
    """One deployment plus every product book, runtime overlay, and related evidence."""

    id: UUID
    strategy_fingerprint: str | None
    strategy_id: UUID | None
    strategy_name: str | None = Field(
        default=None, description="Strategy name captured at start; kept after deletion."
    )
    strategy_deleted: bool = Field(
        default=False,
        description="True for a kept (stopped live) book whose strategy was deleted.",
    )
    portfolio_id: UUID | None = Field(
        default=None,
        description="The portfolio this bot is a sleeve of (ADR 0091); null for a standalone bot.",
    )
    kind: str
    timeframe: str | None
    product_id: str
    mode: str
    status: str
    phase: str = Field(
        description=(
            "Raw worker state machine (flat, pending_entry, open, pending_exit). pending_exit "
            "includes an open book whose TP/SL protection merely rests; read position_state."
        )
    )
    position_state: str = Field(
        default="flat",
        description=(
            "Operator reading across books (ADR 0097): flat, entering, open_protected, "
            "open_unprotected, open_unverified, or exiting (the worst book wins)."
        ),
    )
    exit_in_flight: bool = Field(
        default=False, description="True when any book's exit is being sent (ADR 0097)."
    )
    cash: str
    paper_starting_cash: str | None
    maker_fee_rate: str | None = None
    taker_fee_rate: str | None = None
    last_evaluated_bar: str | None
    last_signal: str | None
    mismatch_detail: str | None
    pending_entry_bars: int
    bars_held: int
    lifecycle_command: str
    daily_loss_latched: bool
    drawdown_latched: bool
    revision: int
    worker_lease_held: bool
    created_at: str
    updated_at: str
    position: PositionResponse | None = Field(
        default=None,
        description=(
            "Compatibility-only focused book: the primary product when that book is "
            "open, otherwise the sole open book. Always includes product_id. Read "
            "`positions` for the full inventory."
        ),
    )
    positions: tuple[PositionResponse, ...] = ()
    instrument_runtimes: tuple[InstrumentRuntimeResponse, ...] = ()
    book_totals: DeploymentBookTotalsResponse = Field(default_factory=DeploymentBookTotalsResponse)
    capital: DeploymentCapitalResponse = Field(default_factory=DeploymentCapitalResponse)
    ledger: DeploymentLedgerSummaryResponse | None = None
    orders: tuple[OrderResponse, ...] = ()
    fills: tuple[FillResponse, ...] = ()


class DeploymentListResponse(BaseModel):
    """Newest-first deployment summaries without historical orders or fills."""

    deployments: tuple[DeploymentResponse, ...]
    limit: int
    offset: int
    returned: int


class FillListResponse(BaseModel):
    """One cursor page of fills for one deployment."""

    fills: tuple[FillResponse, ...]
    limit: int
    returned: int
    next_cursor: str | None = None


class OrderListResponse(BaseModel):
    """One cursor page of orders for one deployment."""

    orders: tuple[OrderResponse, ...]
    limit: int
    returned: int
    next_cursor: str | None = None


@router.post("", response_model=DeploymentResponse, status_code=status.HTTP_201_CREATED)
async def post_deployment(
    body: CreateDeploymentRequest,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    publication_store: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
    risk_store: Annotated[RiskPolicyStore, Depends(get_risk_policy_store)],
    strategies: Annotated[StrategyStore, Depends(get_strategy_store)],
    watchlist: Annotated[MarketDataWatchlistStore, Depends(get_market_data_watchlist_store)],
) -> DeploymentResponse:
    """Snapshot the strategy's current rules and start a running paper or live book.

    A strategy that reads reference instruments (ADR 0096) starts only when every
    reference series is on the enabled market-data watchlist (409 otherwise, naming
    the ``thytrader-data watch-add`` command).
    """
    require_live_acknowledgement(body.mode, acknowledged=body.i_understand_live)
    snapshot = await snapshot_for_start(strategies, body.strategy_id)
    try:
        deployment = await create_deployment(
            store=store,
            publication_store=publication_store,
            strategy_fingerprint=snapshot.strategy_fingerprint,
            mode=body.mode,
            paper_starting_cash=parse_decimal(body.paper_starting_cash),
            paper_maker_fee_rate=parse_decimal(body.maker_fee_rate, field="maker_fee_rate"),
            paper_taker_fee_rate=parse_decimal(body.taker_fee_rate, field="taker_fee_rate"),
            live_allowed=runtime.settings.coinbase_api_key_name is not None,
            risk_store=risk_store,
            reference_watches=ReferenceWatchlist(
                store=watchlist, provider=ingestion_provider(runtime.settings)
            ),
        )
    except ExecutionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    except ExecutionStoreError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(error).lower()
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(status_code=code, detail=str(error)) from None
    await _append_runtime_audit(
        audit,
        action="start_live" if deployment.mode is DeploymentMode.LIVE else "start_paper",
        deployment=deployment,
    )
    snapshot = await store.get_deployment(deployment.id)
    extra = await _covered_products(publication_store, snapshot.deployment)
    return await _snapshot_response(
        snapshot,
        publication_store,
        extra_product_ids=extra,
    )


@router.get("", response_model=DeploymentListResponse)
async def list_deployments(
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    publication_store: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    strategy_id: Annotated[UUID | None, Query()] = None,
) -> DeploymentListResponse:
    """Return deployment summaries, optionally only one strategy's bots (indexed)."""
    try:
        if strategy_id is None:
            deployment_rows = await store.list_deployments(limit=limit, offset=offset)
        else:
            owned = await store.list_by_strategy(str(strategy_id))
            deployment_rows = owned[offset : offset + limit]
    except ExecutionStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from None
    bodies: list[DeploymentResponse] = []
    for item in deployment_rows:
        try:
            summary = await store.get_deployment_summary(item.id)
        except ExecutionStoreError:
            continue
        extra = await _covered_products(publication_store, item)
        bodies.append(
            await _summary_response(
                summary,
                publication_store,
                extra_product_ids=extra,
            )
        )
    return DeploymentListResponse(
        deployments=tuple(bodies),
        limit=limit,
        offset=offset,
        returned=len(bodies),
    )


@router.get("/{deployment_id}", response_model=DeploymentResponse)
async def get_deployment(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    publication_store: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    journal: Annotated[DecisionJournalStore | None, Depends(get_optional_decision_journal_store)],
    detail: Annotated[Literal["summary", "full"], Query()] = "summary",
) -> DeploymentResponse:
    """Return one deployment; ``full`` includes every order and fill.

    Open books carry a last-bar ``mark_price`` and gross ``unrealized_pnl`` (ADR 0098).
    """
    if detail == "full":
        snapshot = await _require_snapshot(store, deployment_id)
        extra = await _covered_products(publication_store, snapshot.deployment)
        response = await _snapshot_response(
            snapshot,
            publication_store,
            extra_product_ids=extra,
        )
        return await _with_book_marks(response, snapshot, journal)
    try:
        summary = await store.get_deployment_summary(deployment_id)
    except ExecutionStoreError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(error).lower()
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(status_code=code, detail=str(error)) from None
    extra = await _covered_products(publication_store, summary.deployment)
    response = await _summary_response(
        summary,
        publication_store,
        extra_product_ids=extra,
    )
    return await _with_book_marks(response, _summary_as_snapshot(summary), journal)


async def _with_book_marks(
    response: DeploymentResponse,
    snapshot: DeploymentSnapshot,
    journal: DecisionJournalStore | None,
) -> DeploymentResponse:
    """Stamp each open book (and the compatibility ``position``) with its last-bar mark."""
    marks = {} if journal is None else await last_bar_marks(journal, snapshot)
    if not marks:
        return response
    positions = tuple(_marked_position(item, marks) for item in response.positions)
    position = None if response.position is None else _marked_position(response.position, marks)
    return response.model_copy(update={"positions": positions, "position": position})


def _marked_position(item: PositionResponse, marks: dict[str, BookMark]) -> PositionResponse:
    """One position row with ``mark_price``, ``marked_at``, and ``unrealized_pnl`` when marked."""
    mark = marks.get(item.product_id)
    if mark is None:
        return item
    pnl = signed_unrealized_pnl(
        quantity=Decimal(item.quantity),
        entry_price=Decimal(item.entry_price),
        side=PositionSide(item.side),
        mark=mark.price,
    )
    return item.model_copy(
        update={
            "mark_price": format(mark.price, "f"),
            "marked_at": mark.bar_closes_at.isoformat(),
            "unrealized_pnl": format(pnl, "f"),
        }
    )


@router.get("/{deployment_id}/fills", response_model=FillListResponse)
async def list_deployment_fills(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query()] = None,
) -> FillListResponse:
    """Return one cursor page of fills for one deployment."""
    await _require_deployment_row(store, deployment_id)
    try:
        page = await store.list_fills(deployment_id, limit=limit, cursor=cursor)
    except ExecutionStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from None
    product_map = dict(page.order_products)
    try:
        summary = await store.get_deployment_summary(deployment_id)
    except ExecutionStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from None
    return FillListResponse(
        fills=tuple(
            _fill_response(
                fill,
                product_id=resolved_product_id(
                    product_map.get(fill.order_id, ""),
                    summary.deployment,
                ),
            )
            for fill in page.fills
        ),
        limit=limit,
        returned=len(page.fills),
        next_cursor=page.next_cursor,
    )


@router.get("/{deployment_id}/orders", response_model=OrderListResponse)
async def list_deployment_orders(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query()] = None,
) -> OrderListResponse:
    """Return one cursor page of orders for one deployment."""
    await _require_deployment_row(store, deployment_id)
    try:
        page = await store.list_orders(deployment_id, limit=limit, cursor=cursor)
        summary = await store.get_deployment_summary(deployment_id)
    except ExecutionStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from None
    return OrderListResponse(
        orders=tuple(
            _order_response(
                order,
                product_id=resolved_product_id(order.product_id, summary.deployment),
            )
            for order in page.orders
        ),
        limit=limit,
        returned=len(page.orders),
        next_cursor=page.next_cursor,
    )


@router.post("/{deployment_id}/pause", response_model=DeploymentResponse)
async def pause_deployment(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    publication_store: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> DeploymentResponse:
    """Pause a running deployment so the worker skips new orders."""
    return await _set_status(
        store, audit, deployment_id, DeploymentStatus.PAUSED, publication_store
    )


@router.post("/{deployment_id}/resume", response_model=DeploymentResponse)
async def resume_deployment(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    publication_store: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
    body: Annotated[ResumeDeploymentRequest | None, Body()] = None,
) -> DeploymentResponse:
    """Resume a paused deployment; a live book also needs ``i_understand_live=true``."""
    acknowledged = body.i_understand_live if body is not None else False
    try:
        current = await store.get_deployment(deployment_id)
    except ExecutionStoreError:
        current = None
    if current is not None:
        require_live_acknowledgement(current.deployment.mode, acknowledged=acknowledged)
    return await _set_status(
        store, audit, deployment_id, DeploymentStatus.RUNNING, publication_store
    )


@router.post("/{deployment_id}/stop", response_model=DeploymentResponse)
async def stop_deployment(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    publication_store: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
    flatten: Annotated[
        bool,
        Query(
            description=(
                "When true, marketably exit inventory then cancel remainders. "
                "Default stop is managed shutdown: keep protective brackets and residual occupancy."
            )
        ),
    ] = False,
) -> DeploymentResponse:
    """Stop a deployment; default is managed shutdown, not flatten."""
    return await _set_status(
        store,
        audit,
        deployment_id,
        DeploymentStatus.STOPPED,
        publication_store,
        flatten=flatten,
    )


@router.post("/{deployment_id}/reset-breaker-latches", response_model=DeploymentResponse)
async def post_reset_breaker_latches(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    publication_store: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> DeploymentResponse:
    """Clear latched daily-loss and drawdown breakers after explicit operator reset."""
    try:
        snapshot = await reset_breaker_latches(store=store, deployment_id=deployment_id)
    except ExecutionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    except ExecutionStoreError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(error).lower()
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(status_code=code, detail=str(error)) from None
    await _append_runtime_audit(
        audit,
        action="reset_breaker_latches",
        deployment=snapshot.deployment,
    )
    extra = await _covered_products(publication_store, snapshot.deployment)
    return await _snapshot_response(
        snapshot,
        publication_store,
        extra_product_ids=extra,
    )


async def _set_status(
    store: ExecutionStore,
    audit: AuditEventStore,
    deployment_id: UUID,
    status_value: DeploymentStatus,
    publication_store: StrategySnapshotStore,
    *,
    flatten: bool = False,
) -> DeploymentResponse:
    """Apply one status change and return the resulting snapshot."""
    try:
        snapshot = await set_deployment_status(
            store=store,
            deployment_id=deployment_id,
            status=status_value,
            flatten=flatten,
        )
    except ExecutionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    except ExecutionStoreError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(error).lower()
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(status_code=code, detail=str(error)) from None
    await _append_runtime_audit(
        audit,
        action=_status_action(status_value),
        deployment=snapshot.deployment,
    )
    extra = await _covered_products(publication_store, snapshot.deployment)
    return await _snapshot_response(
        snapshot,
        publication_store,
        extra_product_ids=extra,
    )


def _status_action(status_value: DeploymentStatus) -> str:
    """Map a status change onto a stable audit action name."""
    if status_value is DeploymentStatus.PAUSED:
        return "pause_deployment"
    if status_value is DeploymentStatus.STOPPED:
        return "stop_deployment"
    return "resume_deployment"


async def _append_runtime_audit(
    audit: AuditEventStore,
    *,
    action: str,
    deployment: Deployment,
) -> None:
    """Record one runtime control action without cash, quantities, or secrets."""
    event = AuditEvent(
        occurred_at=datetime.now(UTC),
        category=AuditEventCategory.RUNTIME,
        action=action,
        outcome=AuditEventOutcome.SUCCESS,
        detail=(
            f"deployment_id={deployment.id} mode={deployment.mode.value} "
            f"status={deployment.status.value} kind={deployment.kind.value} "
            f"fingerprint={deployment.strategy_fingerprint}"
        ),
        product_id=deployment.product_id,
    )
    await audit.append(event)


async def _require_snapshot(store: ExecutionStore, deployment_id: UUID) -> DeploymentSnapshot:
    """Load one snapshot or map storage errors into HTTP failures."""
    try:
        return await store.get_deployment(deployment_id)
    except ExecutionStoreError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(error).lower()
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(status_code=code, detail=str(error)) from None


async def _require_deployment_row(store: ExecutionStore, deployment_id: UUID) -> Deployment:
    """Load one deployment row or map storage errors into HTTP failures."""
    try:
        summary = await store.get_deployment_summary(deployment_id)
    except ExecutionStoreError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(error).lower()
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(status_code=code, detail=str(error)) from None
    return summary.deployment


async def _covered_products(
    publication_store: StrategySnapshotStore, deployment: Deployment
) -> tuple[str, ...]:
    """Return published covered products, or the primary id when identity is missing."""
    fingerprint = deployment.strategy_fingerprint
    if fingerprint is None:
        return (deployment.product_id,)
    loader = getattr(publication_store, "load", None)
    if loader is None:
        return (deployment.product_id,)
    try:
        published = await loader(fingerprint)
    except StrategySnapshotError, ExecutionStoreError:
        return (deployment.product_id,)
    return covered_product_ids(published.definition)


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
        initial_equity=_optional_decimal_string(deployment.initial_equity),
        baseline_equity=_optional_decimal_string(deployment.baseline_equity),
        high_water_mark_equity=_optional_decimal_string(deployment.high_water_mark_equity),
        utc_day_open_equity=_optional_decimal_string(deployment.utc_day_open_equity),
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


async def _snapshot_response(
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
            "ledger": _ledger_summary_response(ledger),
            "orders": tuple(
                _order_response(order, product_id=order_products[order.id])
                for order in snapshot.orders
            ),
            "fills": tuple(
                _fill_response(
                    fill,
                    product_id=order_products.get(fill.order_id, snapshot.deployment.product_id),
                )
                for fill in snapshot.fills
            ),
        }
    )


async def _summary_response(
    summary: DeploymentSummarySnapshot,
    publication_store: StrategySnapshotStore | None = None,
    *,
    extra_product_ids: tuple[str, ...] = (),
) -> DeploymentResponse:
    """Serialize one deployment summary without historical orders or fills."""
    snapshot = _summary_as_snapshot(summary)
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
            "ledger": _ledger_summary_response(ledger),
            "orders": (),
            "fills": (),
        }
    )


def _summary_as_snapshot(summary: DeploymentSummarySnapshot) -> DeploymentSnapshot:
    """Project a summary row into a snapshot shape for ledger and protection helpers."""
    return DeploymentSnapshot(
        deployment=summary.deployment,
        position=summary.position,
        positions=summary.positions,
        instrument_runtimes=summary.instrument_runtimes,
        orders=summary.open_orders,
    )


def _ledger_summary_response(ledger: DeploymentLedger) -> DeploymentLedgerSummaryResponse:
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
        _position_response(item, snapshot, compatibility_focus=False)
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
    return _position_response(focused, snapshot, compatibility_focus=True)


def _position_response(
    position: Position,
    snapshot: DeploymentSnapshot,
    *,
    compatibility_focus: bool,
) -> PositionResponse:
    """Serialize one open long or short product book."""
    product_id = resolved_product_id(position.product_id, snapshot.deployment)
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
        protection_status=book_protection_status(
            snapshot, product_id=product_id, position=position
        ).value,
        position_state=book_position_state(
            snapshot, product_id=product_id, position=position, phase=RuntimePhase.OPEN
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


def _order_response(order: Order, *, product_id: str) -> OrderResponse:
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


def _fill_response(fill: Fill, *, product_id: str) -> FillResponse:
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
