"""HTTP contract for journals, hooks, monitor, notify, and experiential models."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves this annotation at runtime.

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, ValidationError

from thytrader.api.dependencies import (
    get_audit_event_store,
    get_backtest_result_store,
    get_dataset_store,
    get_execution_store,
    get_memory_store,
    get_notification_sender,
    get_runtime_state,
)
from thytrader.execution.store import ExecutionStore  # noqa: TC001 - FastAPI Depends.
from thytrader.market_data.datasets import DatasetStore  # noqa: TC001 - FastAPI Depends.
from thytrader.memory.evidence import LocalEvidenceResolver
from thytrader.memory.models import (
    ExperientialModel,
    ExperientialTrainWrite,
    JournalEntry,
    JournalWrite,
    MemoryStatus,
    MonitorSnapshot,
    NotificationRecord,
    NotificationWrite,
    PatternObservation,
    PatternWrite,
    SentimentSnapshot,
    SentimentWrite,
)
from thytrader.memory.notify import NotificationSender  # noqa: TC001 - FastAPI Depends.
from thytrader.memory.service import (
    build_monitor,
    kind_or_none,
    load_memory_status,
    origin_or_none,
    record_journal,
    record_pattern,
    record_sentiment,
    storage_label,
    submit_notification,
)
from thytrader.memory.store import ExperientialMemoryStore, MemoryStoreError
from thytrader.memory.training import ExperientialTrainingError, train_experiential_model
from thytrader.persistence.audit_events import AuditEventStore  # noqa: TC001 - FastAPI Depends.
from thytrader.persistence.backtest_results import (
    BacktestResultReader,  # noqa: TC001 - FastAPI Depends.
)
from thytrader.runtime import RuntimeState  # noqa: TC001 - FastAPI Depends.

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


class JournalListResponse(BaseModel):
    """Newest-first journal listing."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    journals: tuple[JournalEntry, ...]


class SentimentListResponse(BaseModel):
    """Newest-first sentiment listing."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    sentiment: tuple[SentimentSnapshot, ...]


class PatternListResponse(BaseModel):
    """Newest-first pattern listing."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    patterns: tuple[PatternObservation, ...]


class NotificationListResponse(BaseModel):
    """Newest-first notification listing."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    notifications: tuple[NotificationRecord, ...]


class ModelListResponse(BaseModel):
    """Newest-first trained experiential-model listing."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    models: tuple[ExperientialModel, ...]


@router.get("", response_model=MemoryStatus)
async def get_memory_status(
    store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
) -> MemoryStatus:
    """Return counts and redacted notifier configuration."""
    try:
        return await load_memory_status(
            store,
            runtime.settings,
            storage=storage_label(store),
        )
    except MemoryStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from None


@router.get("/monitor", response_model=MonitorSnapshot)
async def get_memory_monitor(
    store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
) -> MonitorSnapshot:
    """Return deployments, recent journals, and notification delivery."""
    try:
        return await build_monitor(
            store,
            execution,
            runtime.settings,
            storage=storage_label(store),
        )
    except MemoryStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from None


@router.get("/journals", response_model=JournalListResponse)
async def get_journals(
    store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
    origin: Annotated[str | None, Query()] = None,
    kind: Annotated[str | None, Query()] = None,
) -> JournalListResponse:
    """List newest-first journals, optionally filtered by origin and kind."""
    try:
        rows = await store.list_journals(origin=origin_or_none(origin), kind=kind_or_none(kind))
    except (ValueError, MemoryStoreError) as error:
        raise _read_error(error) from None
    return JournalListResponse(journals=rows)


@router.post("/journals", status_code=status.HTTP_201_CREATED)
async def post_journal(
    body: JournalWrite,
    store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> JournalEntry:
    """Append one origin-attributed journal row."""
    try:
        return await record_journal(store, audit, body)
    except (ValueError, ValidationError, MemoryStoreError) as error:
        raise _write_error(error) from None


@router.get("/sentiment", response_model=SentimentListResponse)
async def get_sentiment(
    store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
    origin: Annotated[str | None, Query()] = None,
) -> SentimentListResponse:
    """List newest-first sentiment snapshots."""
    try:
        rows = await store.list_sentiment(origin=origin_or_none(origin))
    except (ValueError, MemoryStoreError) as error:
        raise _read_error(error) from None
    return SentimentListResponse(sentiment=rows)


@router.post("/sentiment", status_code=status.HTTP_201_CREATED)
async def post_sentiment(
    body: SentimentWrite,
    store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> SentimentSnapshot:
    """Append one sentiment hook."""
    try:
        return await record_sentiment(store, audit, body)
    except (ValueError, ValidationError, MemoryStoreError) as error:
        raise _write_error(error) from None


@router.get("/patterns", response_model=PatternListResponse)
async def get_patterns(
    store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
    origin: Annotated[str | None, Query()] = None,
    pattern_key: Annotated[str | None, Query()] = None,
) -> PatternListResponse:
    """List newest-first pattern observations."""
    try:
        rows = await store.list_patterns(origin=origin_or_none(origin), pattern_key=pattern_key)
    except (ValueError, MemoryStoreError) as error:
        raise _read_error(error) from None
    return PatternListResponse(patterns=rows)


@router.post("/patterns", status_code=status.HTTP_201_CREATED)
async def post_pattern(
    body: PatternWrite,
    store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> PatternObservation:
    """Append one pattern-learning hook."""
    try:
        return await record_pattern(store, audit, body)
    except (ValueError, ValidationError, MemoryStoreError) as error:
        raise _write_error(error) from None


@router.get("/notifications", response_model=NotificationListResponse)
async def get_notifications(
    store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
    origin: Annotated[str | None, Query()] = None,
) -> NotificationListResponse:
    """List newest-first notification attempts."""
    try:
        rows = await store.list_notifications(origin=origin_or_none(origin))
    except (ValueError, MemoryStoreError) as error:
        raise _read_error(error) from None
    return NotificationListResponse(notifications=rows)


@router.post("/notifications", status_code=status.HTTP_201_CREATED)
async def post_notification(
    body: NotificationWrite,
    store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
    sender: Annotated[NotificationSender, Depends(get_notification_sender)],
) -> NotificationRecord:
    """Request one user notification through the configured provider."""
    try:
        return await submit_notification(store, audit, sender, body)
    except (ValueError, ValidationError, MemoryStoreError) as error:
        raise _write_error(error) from None


@router.get("/models", response_model=ModelListResponse)
async def get_models(
    store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
) -> ModelListResponse:
    """List newest-first trained experiential models."""
    try:
        rows = await store.list_models()
    except MemoryStoreError as error:
        raise _read_error(error) from None
    return ModelListResponse(models=rows)


@router.get("/models/{model_id}", response_model=ExperientialModel)
async def get_model(
    model_id: UUID,
    store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
) -> ExperientialModel:
    """Return one trained model or 404."""
    try:
        model = await store.get_model(model_id)
    except MemoryStoreError as error:
        raise _read_error(error) from None
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model was not found.")
    return model


@router.post("/models", status_code=status.HTTP_201_CREATED, response_model=ExperientialModel)
async def post_model(
    body: ExperientialTrainWrite,
    store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
    backtests: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
    datasets: Annotated[DatasetStore, Depends(get_dataset_store)],
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
) -> ExperientialModel:
    """Train one fail-closed model from attributed local journal evidence."""
    resolver = LocalEvidenceResolver(
        backtests=backtests,
        datasets=datasets,
        execution=execution,
    )
    try:
        return await train_experiential_model(store, audit, resolver, body)
    except (ExperientialTrainingError, ValueError, ValidationError, MemoryStoreError) as error:
        raise _write_error(error) from None


def _read_error(error: Exception) -> HTTPException:
    """Map list failures to 422 or 503."""
    if isinstance(error, MemoryStoreError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))


def _write_error(error: Exception) -> HTTPException:
    """Map write failures to 422 or 503."""
    if isinstance(error, MemoryStoreError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
