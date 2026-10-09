"""Paper and live deployment HTTP contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from thytrader.api.dependencies import (
    get_audit_event_store,
    get_execution_store,
    get_inventory_adoption_store,
    get_market_data_service,
    get_market_data_watchlist_store,
    get_memory_store,
    get_optional_decision_journal_store,
    get_quote_reader,
    get_risk_policy_store,
    get_runtime_state,
    get_strategy_snapshot_store,
    get_strategy_store,
)
from thytrader.api.live_ack import require_live_acknowledgement
from thytrader.api.paper_fees import get_paper_fee_source
from thytrader.api.routes.deployment_adoption import start_adopting
from thytrader.api.routes.deployment_models import (
    CreateDeploymentRequest,
    DeploymentListResponse,
    DeploymentResponse,
    FillListResponse,
    OrderListResponse,
    PositionResponse,
    ResumeDeploymentRequest,
)
from thytrader.api.routes.deployment_serializers import (
    fill_response,
    ledger_summary_response,
    order_response,
    snapshot_response,
    summary_response,
)
from thytrader.api.strategy_http import snapshot_for_start
from thytrader.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)
from thytrader.data_control.service import ingestion_provider
from thytrader.exchanges.protocols import ExchangeAccount
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.book_marks import (
    entry_fees_by_product,
    last_bar_marks,
    signed_unrealized_pnl,
)
from thytrader.execution.decision_store import (
    DecisionJournalStore,
)
from thytrader.execution.paper_fees import PaperFeeSource
from thytrader.execution.service import (
    ReferenceWatchlist,
    create_deployment,
    parse_decimal,
    reset_breaker_latches,
    set_deployment_status,
)
from thytrader.fleet_control.inventory import read_stable_inventory
from thytrader.market_data.service import MarketDataService
from thytrader.market_data.watchlist import (
    MarketDataWatchlistStore,
)
from thytrader.memory.store import ExperientialMemoryStore
from thytrader.risk.store import RiskPolicyStore
from thytrader.runtime import RuntimeState
from thytrader.strategies.library import StrategyStore
from thytrader.strategies.models import covered_product_ids
from thytrader.strategies.snapshots import (
    StrategySnapshotError,
    StrategySnapshotStore,
)
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionConflictError,
    ExecutionStoreError,
    PositionSide,
    resolved_product_id,
    summary_as_snapshot,
)
from thytrader.trading.store import ExecutionStore, InventoryAdoptionStore

if TYPE_CHECKING:
    from thytrader.execution.book_marks import BookMark

__all__ = [
    "CreateDeploymentRequest",
    "DeploymentListResponse",
    "DeploymentResponse",
    "FillListResponse",
    "OrderListResponse",
    "PositionResponse",
    "ResumeDeploymentRequest",
    "require_deployment_row",
    "router",
]

router = APIRouter(prefix="/api/v1/deployments", tags=["deployments"])


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
    paper_fee_source: Annotated[PaperFeeSource, Depends(get_paper_fee_source)],
    adoption_store: Annotated[InventoryAdoptionStore | None, Depends(get_inventory_adoption_store)],
    market_data: Annotated[MarketDataService, Depends(get_market_data_service)],
    quote_reader: Annotated[ExchangeAccount | None, Depends(get_quote_reader)],
    memory_store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
) -> DeploymentResponse:
    """Snapshot the strategy's current rules and start a running paper or live book.

    With ``adopt_holdings`` (live only, ADR 0124) the bot starts already holding that
    quantity, or all, of the account's unmanaged coins; the book and the adoption commit
    together, and the worker's next cycle places the strategy's protection.

    A paper start that omits both fee rates uses the Coinbase account's rates, and is
    refused (409) when those cannot be read.

    A strategy that reads reference instruments (ADR 0096) starts only when every
    reference series is on the enabled market-data watchlist (409 otherwise, naming
    the ``thytrader-data watch-add`` command).
    """
    require_live_acknowledgement(body.mode, acknowledged=body.i_understand_live)
    snapshot = await snapshot_for_start(strategies, body.strategy_id)
    reference_watches = ReferenceWatchlist(
        store=watchlist, provider=ingestion_provider(runtime.settings)
    )
    try:
        if body.adopt_holdings is not None:
            with execution_audit_scope(audit):
                deployment = await start_adopting(
                    mode=body.mode,
                    adopt_holdings=body.adopt_holdings,
                    snapshot=snapshot,
                    store=store,
                    publication_store=publication_store,
                    live_allowed=runtime.settings.coinbase_api_key_name is not None,
                    risk_store=risk_store,
                    reference_watches=reference_watches,
                    adoption_store=adoption_store,
                    market_data=market_data,
                    quote_reader=quote_reader,
                    memory_store=memory_store,
                )
        else:
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
                reference_watches=reference_watches,
                paper_fee_source=paper_fee_source,
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
    return await snapshot_response(
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
    as_of: Annotated[
        str | None,
        Query(description="Timezone-aware UTC snapshot from a previous page. Omit for a new read."),
    ] = None,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> DeploymentListResponse:
    """Return one stable inventory page. Default limit 50 is not the whole fleet."""
    pinned = _parse_as_of(as_of)
    try:
        page = await read_stable_inventory(
            store,
            limit=limit,
            offset=offset,
            strategy_id=strategy_id,
            as_of=pinned,
            cursor=cursor,
        )
    except ExecutionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    except ExecutionStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from None
    bodies: list[DeploymentResponse] = []
    for item in page.deployments:
        try:
            summary = await store.get_deployment_summary(item.id)
        except ExecutionStoreError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(error),
            ) from None
        if (
            summary.deployment.mode,
            summary.deployment.kind,
            summary.deployment.strategy_id,
            summary.deployment.strategy_fingerprint,
            summary.deployment.product_id,
        ) != (
            item.mode,
            item.kind,
            item.strategy_id,
            item.strategy_fingerprint,
            item.product_id,
        ):
            raise HTTPException(
                status_code=409, detail="inventory_changed: deployment reclassified during read."
            )
        extra = await _covered_products(publication_store, item)
        bodies.append(
            await summary_response(
                summary,
                publication_store,
                extra_product_ids=extra,
            )
        )
    return DeploymentListResponse(
        deployments=tuple(bodies),
        limit=page.limit,
        offset=page.offset,
        returned=len(bodies),
        has_more=page.has_more,
        total=page.total,
        order=page.order,
        as_of=page.as_of.isoformat(),
        fingerprint=page.fingerprint,
        next_cursor=page.next_cursor,
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

    Open books carry a last-bar mark, gross PnL, and verified paid-entry-fee net PnL (ADR 0100).
    """
    if detail == "full":
        snapshot = await _require_snapshot(store, deployment_id)
        extra = await _covered_products(publication_store, snapshot.deployment)
        response = await snapshot_response(
            snapshot,
            publication_store,
            extra_product_ids=extra,
        )
        return await _with_book_marks(response, snapshot, journal, store)
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
    response = await summary_response(
        summary,
        publication_store,
        extra_product_ids=extra,
    )
    return await _with_book_marks(response, summary_as_snapshot(summary), journal, store)


async def _with_book_marks(
    response: DeploymentResponse,
    snapshot: DeploymentSnapshot,
    journal: DecisionJournalStore | None,
    store: ExecutionStore,
) -> DeploymentResponse:
    """Mark open books and their aggregate ledger from the same journaled closes."""
    marks = {} if journal is None else await last_bar_marks(journal, snapshot)
    if not marks:
        return response
    fees = await entry_fees_by_product(store, snapshot, marks)
    positions = tuple(_marked_position(item, marks, fees) for item in response.positions)
    position = (
        None if response.position is None else _marked_position(response.position, marks, fees)
    )
    ledger = ledger_from_snapshot(
        snapshot, marks={product_id: mark.price for product_id, mark in marks.items()}
    )
    return response.model_copy(
        update={
            "positions": positions,
            "position": position,
            "ledger": ledger_summary_response(ledger),
        }
    )


def _marked_position(
    item: PositionResponse, marks: dict[str, BookMark], fees: dict[str, Decimal | None]
) -> PositionResponse:
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
    entry_fees = fees.get(item.product_id)
    return item.model_copy(
        update={
            "mark_price": format(mark.price, "f"),
            "marked_at": mark.bar_closes_at.isoformat(),
            "unrealized_pnl": format(pnl, "f"),
            "entry_fees": None if entry_fees is None else format(entry_fees, "f"),
            "unrealized_pnl_net": (None if entry_fees is None else format(pnl - entry_fees, "f")),
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
    await require_deployment_row(store, deployment_id)
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
            fill_response(
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
    await require_deployment_row(store, deployment_id)
    try:
        page = await store.list_orders(deployment_id, limit=limit, cursor=cursor)
        summary = await store.get_deployment_summary(deployment_id)
    except ExecutionStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from None
    return OrderListResponse(
        orders=tuple(
            order_response(
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
    return await snapshot_response(
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
    return await snapshot_response(
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


async def require_deployment_row(store: ExecutionStore, deployment_id: UUID) -> Deployment:
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


def _parse_as_of(value: str | None) -> datetime | None:
    """Parse a pinned inventory snapshot. Naive timestamps are refused."""
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="as_of must be a timezone-aware ISO-8601 timestamp.",
        ) from error
    if parsed.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="as_of must be a timezone-aware ISO-8601 timestamp.",
        )
    return parsed.astimezone(UTC)
