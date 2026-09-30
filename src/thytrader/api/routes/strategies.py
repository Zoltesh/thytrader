"""Browser- and agent-facing HTTP contract for mutable strategies (ADR 0082).

A strategy is one object: create, list, get, save (revision-guarded), clone,
import, delete, and bulk delete. Saving never starts anything; backtest, study,
and deployment start endpoints snapshot the current definition themselves.
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID  # noqa: TC003 - FastAPI resolves this annotation at runtime.

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, JsonValue, StrictBool, StrictInt

from thytrader.api.dependencies import (
    get_audit_event_store,
    get_backtest_result_store,
    get_execution_store,
    get_strategy_store,
)
from thytrader.api.strategy_http import strategy_http_error
from thytrader.backtest.models import BacktestSummary  # noqa: TC001 - Pydantic model field.
from thytrader.execution.models import DeploymentMode, DeploymentStatus, ExecutionStoreError
from thytrader.execution.store import ExecutionStore  # noqa: TC001 - FastAPI Depends.
from thytrader.market_data.models import DatasetTimeframe  # noqa: TC001 - FastAPI Query annotation.
from thytrader.market_data.products import SPOT_PRODUCT_ID_PATTERN
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)
from thytrader.persistence.backtest_results import (
    BacktestResultReader,  # noqa: TC001 - FastAPI resolves this annotation at runtime.
    BacktestResultSummaryView,  # noqa: TC001 - FastAPI resolves this annotation at runtime.
)
from thytrader.research.pagination import decode_offset_cursor, encode_offset_cursor
from thytrader.strategies.authoring import create_template_strategy, new_strategy_identity
from thytrader.strategies.library import (
    MAX_BULK_DELETE,
    BulkDeletionItem,
    StrategyDeletionCounts,
    StrategyLibraryError,
    StrategyRecord,
    StrategyStore,
    bulk_delete_strategies,
    clone_strategy,
    create_strategy_from_definition,
    import_strategy,
    parse_document,
)
from thytrader.strategies.models import StrategyDefinition  # noqa: TC001 - Pydantic field.
from thytrader.strategies.summary import strategy_summary

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.execution.models import Deployment

router = APIRouter(prefix="/api/v1/strategies", tags=["strategies"])
_logger = logging.getLogger(__name__)

PaperLiveStatus = Literal["running", "paused", "stopped", "none", "unavailable"]
_STATUS_LABELS: dict[DeploymentStatus, PaperLiveStatus] = {
    DeploymentStatus.RUNNING: "running",
    DeploymentStatus.PAUSED: "paused",
    DeploymentStatus.STOPPED: "stopped",
}


class ValidationIssueResponse(BaseModel):
    """One problem that keeps a saved document from being startable."""

    loc: str
    message: str


class StrategyValidationResponse(BaseModel):
    """The validation result stored with the current document."""

    valid: bool
    issues: tuple[ValidationIssueResponse, ...] = ()


class StrategyResponse(BaseModel):
    """One mutable strategy: its document, validity, and current fingerprint."""

    strategy_id: UUID
    name: str
    revision: int = Field(ge=1)
    created_at: str
    updated_at: str
    document: dict[str, JsonValue]
    strategy: StrategyDefinition | None = Field(
        description="The validated definition; null while the saved document is invalid."
    )
    validation: StrategyValidationResponse
    current_fingerprint: str | None = Field(
        description="Fingerprint the next backtest/study/deployment snapshot would record."
    )
    summary: str | None
    product_id: str | None
    timeframe: str | None


class StrategyLibraryBacktestResponse(BaseModel):
    """The newest backtest of one strategy, with the snapshot it ran."""

    result_fingerprint: str
    strategy_fingerprint: str
    published_at: str
    summary: BacktestSummary


class StrategyLibraryPaperLiveResponse(BaseModel):
    """Newest paper and live bot status for one strategy."""

    paper: PaperLiveStatus = "none"
    live: PaperLiveStatus = "none"


class StrategyLibraryEntryResponse(BaseModel):
    """One library row: identity, validity, and Build/Test/Paper/Live evidence."""

    strategy_id: UUID
    name: str
    product_id: str | None
    timeframe: str | None
    revision: int
    valid: bool
    current_fingerprint: str | None
    summary: str | None
    created_at: str
    updated_at: str
    backtest: StrategyLibraryBacktestResponse | None
    paper_live: StrategyLibraryPaperLiveResponse
    active_deployment_count: int = Field(ge=0)


class StrategyListResponse(BaseModel):
    """One bounded newest-updated-first page of the strategy library."""

    strategies: tuple[StrategyLibraryEntryResponse, ...]
    limit: int
    returned: int
    total: int
    has_more: bool
    next_cursor: str | None = None


class StrategySaveRequest(BaseModel):
    """One complete document (valid or not) plus the revision it was edited from."""

    document: dict[str, JsonValue]
    revision: Annotated[StrictInt, Field(ge=1)]


class StrategyImportRequest(BaseModel):
    """One strategy JSON document to import as a new strategy."""

    document: dict[str, JsonValue]


class StrategyDeletionCountsResponse(BaseModel):
    """What a deletion removes; live books are kept and only detached."""

    snapshots: int
    backtests: int
    research_runs: int
    studies: int
    research_jobs: int
    dataset_bindings: int
    paper_deployments: int
    live_deployments_kept: int
    allocations_removed: int


class StrategyDeletionResponse(BaseModel):
    """The committed result of deleting one strategy."""

    strategy_id: UUID
    name: str
    outcome: Literal["deleted"] = "deleted"
    counts: StrategyDeletionCountsResponse
    risk_policy_republished: bool


class StrategyBulkDeleteRequest(BaseModel):
    """Up to 100 strategies to delete, or to preview deleting with ``dry_run``."""

    strategy_ids: tuple[UUID, ...] = Field(min_length=1, max_length=MAX_BULK_DELETE)
    confirm: StrictBool = False
    dry_run: StrictBool = False


class StrategyBulkDeleteItemResponse(BaseModel):
    """One strategy's bulk outcome."""

    strategy_id: UUID
    name: str | None
    outcome: Literal["deleted", "would_delete", "blocked", "not_found", "failed"]
    code: str | None
    message: str | None
    deployment_ids: tuple[UUID, ...] = ()
    counts: StrategyDeletionCountsResponse | None
    risk_policy_republished: bool = False


class StrategyBulkDeleteResponse(BaseModel):
    """Per-strategy results; partial failure is reported, never hidden."""

    dry_run: bool
    results: tuple[StrategyBulkDeleteItemResponse, ...]
    deleted: int
    would_delete: int
    blocked: int
    not_found: int
    failed: int


class StrategySnapshotResponse(BaseModel):
    """One immutable snapshot, its owner, and whether it equals the current rules."""

    strategy_fingerprint: str
    strategy_id: UUID | None
    strategy_name: str | None
    strategy: StrategyDefinition
    created_at: str
    is_current: bool


@router.get("", response_model=StrategyListResponse)
async def list_strategies(
    store: Annotated[StrategyStore, Depends(get_strategy_store)],
    result_store: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
    execution_store: Annotated[ExecutionStore, Depends(get_execution_store)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> StrategyListResponse:
    """Return one newest-updated-first library page with batched evidence reads."""
    start = _cursor_offset(cursor)
    try:
        page = await store.list_page(limit=limit, offset=start)
    except StrategyLibraryError as error:
        raise strategy_http_error(error) from None
    identities = [record.strategy_id for record in page.records]
    backtests = await _newest_backtests(identities, result_store)
    runtimes = await _runtime_statuses(identities, execution_store)
    entries = tuple(
        _library_entry(
            record,
            backtests.get(record.strategy_id),
            runtimes.get(record.strategy_id, (StrategyLibraryPaperLiveResponse(), 0)),
        )
        for record in page.records
    )
    has_more = start + len(entries) < page.total
    return StrategyListResponse(
        strategies=entries,
        limit=limit,
        returned=len(entries),
        total=page.total,
        has_more=has_more,
        next_cursor=encode_offset_cursor(start + limit) if has_more else None,
    )


@router.post("", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED)
async def create_strategy(
    store: Annotated[StrategyStore, Depends(get_strategy_store)],
    product_id: Annotated[str, Query(pattern=SPOT_PRODUCT_ID_PATTERN)] = "BTC-USD",
    timeframe: Annotated[DatasetTimeframe, Query()] = "1h",
    template: Annotated[str, Query()] = "ema-trend",
) -> StrategyResponse:
    """Create one strategy from a fail-closed research template (no trading authority)."""
    try:
        definition = create_template_strategy(
            product_id=product_id, timeframe=timeframe, template=template
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "strategy_template_invalid", "message": str(error)},
        ) from None
    try:
        record = await create_strategy_from_definition(store, definition)
    except StrategyLibraryError as error:
        raise strategy_http_error(error) from None
    return strategy_response(record)


@router.post("/import", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED)
async def import_strategy_document(
    body: StrategyImportRequest,
    store: Annotated[StrategyStore, Depends(get_strategy_store)],
) -> StrategyResponse:
    """Create a new strategy (fresh identity) from one imported JSON document."""
    strategy_id, created_at = new_strategy_identity()
    try:
        document = parse_document(body.document)
        record = await import_strategy(
            store, document, strategy_id=strategy_id, created_at=created_at
        )
    except StrategyLibraryError as error:
        raise strategy_http_error(error) from None
    return strategy_response(record)


@router.post(
    "/bulk-delete",
    response_model=StrategyBulkDeleteResponse,
    responses={status.HTTP_400_BAD_REQUEST: {"description": "confirmation_required"}},
)
async def bulk_delete(
    body: StrategyBulkDeleteRequest,
    store: Annotated[StrategyStore, Depends(get_strategy_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> StrategyBulkDeleteResponse:
    """Delete (or with ``dry_run`` preview deleting) several strategies independently."""
    if not body.dry_run and not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "confirmation_required",
                "message": "Bulk delete requires confirm=true (or dry_run=true to preview).",
            },
        )
    identities = tuple(dict.fromkeys(body.strategy_ids))
    report = await bulk_delete_strategies(store, identities, dry_run=body.dry_run)
    if not report.dry_run:
        for item in report.items:
            if item.outcome == "deleted" and item.counts is not None:
                await _audit_deletion(audit, item.strategy_id, item.counts)
    return StrategyBulkDeleteResponse(
        dry_run=report.dry_run,
        results=tuple(_bulk_item_response(item) for item in report.items),
        deleted=report.count("deleted"),
        would_delete=report.count("would_delete"),
        blocked=report.count("blocked"),
        not_found=report.count("not_found"),
        failed=report.count("failed"),
    )


@router.get("/snapshots/{strategy_fingerprint}", response_model=StrategySnapshotResponse)
async def get_strategy_snapshot(
    strategy_fingerprint: str,
    store: Annotated[StrategyStore, Depends(get_strategy_store)],
) -> StrategySnapshotResponse:
    """Return one snapshot so clients can diff it or resolve its owning strategy."""
    try:
        lookup = await store.lookup_snapshot(strategy_fingerprint)
    except StrategyLibraryError as error:
        raise strategy_http_error(error) from None
    return StrategySnapshotResponse(
        strategy_fingerprint=lookup.snapshot.strategy_fingerprint,
        strategy_id=lookup.strategy_id,
        strategy_name=lookup.strategy_name,
        strategy=lookup.snapshot.definition,
        created_at=_iso(lookup.created_at),
        is_current=lookup.is_current,
    )


@router.get("/{strategy_id}", response_model=StrategyResponse)
async def get_strategy(
    strategy_id: UUID,
    store: Annotated[StrategyStore, Depends(get_strategy_store)],
) -> StrategyResponse:
    """Return one strategy's current document and validation state."""
    try:
        record = await store.get(strategy_id)
    except StrategyLibraryError as error:
        raise strategy_http_error(error) from None
    return strategy_response(record)


@router.put("/{strategy_id}", response_model=StrategyResponse)
async def save_strategy(
    strategy_id: UUID,
    body: StrategySaveRequest,
    store: Annotated[StrategyStore, Depends(get_strategy_store)],
) -> StrategyResponse:
    """Save in place; a stale ``revision`` is rejected (409), never overwritten."""
    try:
        document = parse_document(body.document)
        record = await store.save(strategy_id, document, expected_revision=body.revision)
    except StrategyLibraryError as error:
        raise strategy_http_error(error) from None
    return strategy_response(record)


@router.post(
    "/{strategy_id}/clone", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED
)
async def clone_strategy_route(
    strategy_id: UUID,
    store: Annotated[StrategyStore, Depends(get_strategy_store)],
) -> StrategyResponse:
    """Duplicate one strategy into a new identity (no history is copied)."""
    new_id, created_at = new_strategy_identity()
    try:
        record = await clone_strategy(store, strategy_id, strategy_id=new_id, created_at=created_at)
    except StrategyLibraryError as error:
        raise strategy_http_error(error) from None
    return strategy_response(record)


@router.delete("/{strategy_id}", response_model=StrategyDeletionResponse)
async def delete_strategy(
    strategy_id: UUID,
    store: Annotated[StrategyStore, Depends(get_strategy_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> StrategyDeletionResponse:
    """Hard-delete one strategy; 409 while any of its bots is running or paused."""
    try:
        result = await store.delete(strategy_id)
    except StrategyLibraryError as error:
        raise strategy_http_error(error) from None
    await _audit_deletion(audit, strategy_id, result.counts)
    return StrategyDeletionResponse(
        strategy_id=result.strategy_id,
        name=result.name,
        counts=counts_response(result.counts),
        risk_policy_republished=result.risk_policy_republished,
    )


def strategy_response(record: StrategyRecord) -> StrategyResponse:
    """Project one record into its HTTP body."""
    return StrategyResponse(
        strategy_id=record.strategy_id,
        name=record.name,
        revision=record.revision,
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
        document=record.document,
        strategy=record.definition,
        validation=StrategyValidationResponse(
            valid=record.validation.valid,
            issues=tuple(
                ValidationIssueResponse(loc=item.loc, message=item.message)
                for item in record.validation.issues
            ),
        ),
        current_fingerprint=record.current_fingerprint,
        summary=None if record.definition is None else strategy_summary(record.definition),
        product_id=record.product_id,
        timeframe=record.timeframe,
    )


def counts_response(counts: StrategyDeletionCounts) -> StrategyDeletionCountsResponse:
    """Project deletion counts."""
    return StrategyDeletionCountsResponse(
        snapshots=counts.snapshots,
        backtests=counts.backtests,
        research_runs=counts.research_runs,
        studies=counts.studies,
        research_jobs=counts.research_jobs,
        dataset_bindings=counts.dataset_bindings,
        paper_deployments=counts.paper_deployments,
        live_deployments_kept=counts.live_deployments_kept,
        allocations_removed=counts.allocations_removed,
    )


def _library_entry(
    record: StrategyRecord,
    backtest: BacktestResultSummaryView | None,
    runtime: tuple[StrategyLibraryPaperLiveResponse, int],
) -> StrategyLibraryEntryResponse:
    """Project one strategy and its newest evidence into a library row."""
    paper_live, active = runtime
    return StrategyLibraryEntryResponse(
        strategy_id=record.strategy_id,
        name=record.name,
        product_id=record.product_id,
        timeframe=record.timeframe,
        revision=record.revision,
        valid=record.validation.valid,
        current_fingerprint=record.current_fingerprint,
        summary=None if record.definition is None else strategy_summary(record.definition),
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
        backtest=None
        if backtest is None
        else StrategyLibraryBacktestResponse(
            result_fingerprint=backtest.result_fingerprint,
            strategy_fingerprint=backtest.strategy_fingerprint,
            published_at=_iso(backtest.published_at),
            summary=backtest.summary,
        ),
        paper_live=paper_live,
        active_deployment_count=active,
    )


async def _newest_backtests(
    identities: Sequence[UUID], result_store: BacktestResultReader
) -> dict[UUID, BacktestResultSummaryView]:
    """Return each strategy's newest backtest in one batched read when supported."""
    if not identities:
        return {}
    batched = getattr(result_store, "newest_summaries_for_strategy_ids", None)
    newest: dict[UUID, BacktestResultSummaryView] = {}
    try:
        if batched is not None:
            return await batched(list(identities))
        for identity in identities:
            rows = await result_store.list_summaries(strategy_id=identity, limit=1, offset=0)
            if rows:
                newest[identity] = rows[0]
    except Exception as error:  # noqa: BLE001 - evidence enrichment must not fail the page.
        _logger.warning("strategy_library_backtests_unavailable error=%s", type(error).__name__)
        return {}
    return newest


async def _runtime_statuses(
    identities: Sequence[UUID], store: ExecutionStore
) -> dict[UUID, tuple[StrategyLibraryPaperLiveResponse, int]]:
    """Project newest paper/live status and active bot count per strategy (one query)."""
    if not identities:
        return {}
    keys = [str(identity) for identity in identities]
    batched = getattr(store, "list_by_strategy_ids", None)
    try:
        if batched is not None:
            grouped: dict[str, tuple[Deployment, ...]] = await batched(keys)
        else:
            grouped = {key: await store.list_by_strategy(key) for key in keys}
    except ExecutionStoreError:
        unavailable = StrategyLibraryPaperLiveResponse(paper="unavailable", live="unavailable")
        return dict.fromkeys(identities, (unavailable, 0))
    return {identity: _runtime_status(grouped.get(str(identity), ())) for identity in identities}


def _runtime_status(
    items: Sequence[Deployment],
) -> tuple[StrategyLibraryPaperLiveResponse, int]:
    """Summarize one strategy's deployments (already newest first)."""
    paper: PaperLiveStatus = "none"
    live: PaperLiveStatus = "none"
    for item in items:
        if item.mode is DeploymentMode.PAPER and paper == "none":
            paper = _STATUS_LABELS[item.status]
        elif item.mode is DeploymentMode.LIVE and live == "none":
            live = _STATUS_LABELS[item.status]
    active = sum(
        1 for item in items if item.status in {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}
    )
    return StrategyLibraryPaperLiveResponse(paper=paper, live=live), active


def _bulk_item_response(item: BulkDeletionItem) -> StrategyBulkDeleteItemResponse:
    """Project one bulk outcome."""
    return StrategyBulkDeleteItemResponse(
        strategy_id=item.strategy_id,
        name=item.name,
        outcome=item.outcome,
        code=item.code,
        message=item.message,
        deployment_ids=item.deployment_ids,
        counts=None if item.counts is None else counts_response(item.counts),
        risk_policy_republished=item.risk_policy_republished,
    )


async def _audit_deletion(
    audit: AuditEventStore, strategy_id: UUID, counts: StrategyDeletionCounts
) -> None:
    """Record a committed deletion; an audit outage never reverses or hides it."""
    event = AuditEvent(
        occurred_at=datetime.now(UTC),
        category=AuditEventCategory.RESEARCH,
        action="delete_strategy",
        outcome=AuditEventOutcome.SUCCESS,
        detail=(
            f"strategy_id={strategy_id} backtests={counts.backtests} studies={counts.studies} "
            f"paper_deployments={counts.paper_deployments} "
            f"live_deployments_kept={counts.live_deployments_kept} "
            f"allocations_removed={counts.allocations_removed}"
        ),
    )
    try:
        await audit.append(event)
    except Exception as error:  # noqa: BLE001 - the deletion already committed.
        _logger.warning("strategy_delete_audit_failed error=%s", type(error).__name__)


def _cursor_offset(cursor: str | None) -> int:
    """Decode one opaque offset cursor."""
    if cursor is None:
        return 0
    try:
        return decode_offset_cursor(cursor)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "strategy_cursor_invalid",
                "message": "Pagination cursor is malformed.",
            },
        ) from None


def _iso(value: datetime) -> str:
    """Render one UTC instant with a Z suffix."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
