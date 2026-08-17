"""Browser-facing strategy draft and immutable-publication HTTP contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves this annotation at runtime.

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from thytrader.api.dependencies import (
    get_strategy_draft_store,
    get_strategy_publication_catalog,
    get_strategy_publication_store,
)
from thytrader.strategies.authoring import StrategyDraft, StrategyDraftStore, create_reference_draft
from thytrader.strategies.models import (
    AllCondition,
    AnyCondition,
    ComparisonCondition,
    ComparisonOperator,
    IndicatorKind,
    IndicatorOperand,
    LiteralOperand,
    NotCondition,
    StrategyDefinition,
    StrategyStatus,
    strategy_fingerprint,
)
from thytrader.strategies.publication import (
    PublishedStrategy,
    StrategyCatalogEntry,
    StrategyPublicationCatalog,
    StrategyPublicationError,
    StrategyPublicationStore,
)

router = APIRouter(prefix="/api/v1/strategies", tags=["strategies"])


class StrategyDraftResponse(BaseModel):
    """One server-identified strategy draft safe for browser editing."""

    strategy: StrategyDefinition
    revision: int = Field(ge=1)
    summary: str


class StrategyDraftRequest(BaseModel):
    """One complete editable strategy draft supplied by the browser."""

    strategy: StrategyDefinition
    revision: Annotated[int, Field(strict=True, ge=1)]


class StrategyListEntryResponse(BaseModel):
    """One browser-selectable strategy with an operator-readable semantic summary."""

    strategy: StrategyDefinition
    revision: int | None
    strategy_fingerprint: str | None
    archived_at: str | None
    summary: str


class StrategyListResponse(BaseModel):
    """A bounded collection of strategy drafts selected by lifecycle state."""

    strategies: list[StrategyListEntryResponse]


class StrategyArchiveResponse(BaseModel):
    """One immutable publication that is now hidden from active selection."""

    strategy_fingerprint: str
    archived_at: str | None


class StrategyPublishRequest(BaseModel):
    """One complete durable draft supplied for immutable publication."""

    strategy: StrategyDefinition
    revision: Annotated[int, Field(strict=True, ge=1)]


class StrategyPublishResponse(BaseModel):
    """One immutable strategy version returned after authoritative persistence."""

    strategy_fingerprint: str
    strategy: StrategyDefinition


@router.post("", response_model=StrategyDraftResponse, status_code=status.HTTP_201_CREATED)
async def create_strategy_draft(
    store: Annotated[StrategyDraftStore, Depends(get_strategy_draft_store)],
) -> StrategyDraftResponse:
    """Create and durably save the conservative reference draft without trading authority."""
    definition = create_reference_draft()
    try:
        draft = await store.create_draft(definition)
    except RuntimeError, TypeError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy draft storage is unavailable.",
        ) from None
    draft = _require_exact_draft(
        draft,
        expected=definition,
        expected_revision=1,
        detail="Strategy draft storage is unavailable.",
    )
    return StrategyDraftResponse(
        strategy=draft.definition,
        revision=draft.revision,
        summary=_strategy_summary(draft.definition),
    )


@router.get("", response_model=StrategyListResponse)
async def list_strategies(
    draft_store: Annotated[StrategyDraftStore, Depends(get_strategy_draft_store)],
    publication_catalog: Annotated[
        StrategyPublicationCatalog, Depends(get_strategy_publication_catalog)
    ],
    strategy_status: Annotated[StrategyStatus, Query(alias="status")] = StrategyStatus.DRAFT,
    include_archived: bool = False,
) -> StrategyListResponse:
    """List recoverable drafts or immutable publications with explicit archive visibility."""
    try:
        if strategy_status is StrategyStatus.DRAFT:
            drafts = await draft_store.list_drafts()
            entries = []
            for draft in drafts:
                validated = _require_exact_draft(
                    draft,
                    detail="Strategy lifecycle storage is unavailable.",
                )
                entries.append(
                    StrategyListEntryResponse(
                        strategy=validated.definition,
                        revision=validated.revision,
                        strategy_fingerprint=None,
                        archived_at=None,
                        summary=_strategy_summary(validated.definition),
                    )
                )
        elif strategy_status is StrategyStatus.PUBLISHED:
            entries = []
            for entry in await publication_catalog.list_published(
                include_archived=include_archived
            ):
                definition = _require_exact_catalog_entry(entry)
                entries.append(
                    StrategyListEntryResponse(
                        strategy=definition,
                        revision=None,
                        strategy_fingerprint=entry.strategy_fingerprint,
                        archived_at=(
                            entry.archived_at.isoformat() if entry.archived_at is not None else None
                        ),
                        summary=_strategy_summary(definition),
                    )
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Archived strategy definitions are available only through published history."
                ),
            )
    except RuntimeError, TypeError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy lifecycle storage is unavailable.",
        ) from None
    return StrategyListResponse(strategies=entries)


@router.put(
    "/{strategy_id}/versions/{version}",
    response_model=StrategyDraftResponse,
)
async def save_strategy_draft(
    strategy_id: UUID,
    version: int,
    request: StrategyDraftRequest,
    store: Annotated[StrategyDraftStore, Depends(get_strategy_draft_store)],
) -> StrategyDraftResponse:
    """Validate and persist one matching editable draft without publication authority."""
    draft = request.strategy
    if (
        draft.strategy_id != strategy_id
        or draft.version != version
        or draft.status is not StrategyStatus.DRAFT
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only the matching editable draft can be saved.",
        )
    try:
        saved = await store.save_draft(draft, expected_revision=request.revision)
    except (RuntimeError, TypeError, ValueError) as error:
        if str(error) == "Strategy draft was not found.":
            status_code = status.HTTP_404_NOT_FOUND
            detail = "Strategy draft was not found."
        elif str(error) == "Strategy draft revision conflict.":
            status_code = status.HTTP_409_CONFLICT
            detail = "Strategy draft changed; reload before saving."
        else:
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            detail = "Strategy draft storage is unavailable."
        raise HTTPException(status_code=status_code, detail=detail) from None
    saved = _require_exact_draft(
        saved,
        expected=draft,
        expected_revision=request.revision + 1,
        detail="Strategy draft storage is unavailable.",
    )
    return StrategyDraftResponse(
        strategy=saved.definition,
        revision=saved.revision,
        summary=_strategy_summary(saved.definition),
    )


@router.post(
    "/{strategy_fingerprint}/archive",
    response_model=StrategyArchiveResponse,
)
async def archive_strategy(
    strategy_fingerprint: str,
    store: Annotated[StrategyPublicationCatalog, Depends(get_strategy_publication_catalog)],
) -> StrategyArchiveResponse:
    """Permanently hide immutable evidence from active browser selection without altering it."""
    try:
        archived = await store.archive(strategy_fingerprint)
    except (RuntimeError, TypeError, ValueError) as error:
        status_code = (
            status.HTTP_404_NOT_FOUND
            if str(error) == "Published strategy was not found."
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        detail = (
            "Published strategy was not found."
            if status_code == status.HTTP_404_NOT_FOUND
            else "Strategy publication catalog is unavailable."
        )
        raise HTTPException(status_code=status_code, detail=detail) from None
    _require_exact_catalog_entry(
        archived,
        expected_fingerprint=strategy_fingerprint,
        require_archive_marker=True,
    )
    return StrategyArchiveResponse(
        strategy_fingerprint=strategy_fingerprint,
        archived_at=(
            archived.archived_at.isoformat() if archived.archived_at is not None else None
        ),
    )


@router.post(
    "/{strategy_id}/publish",
    response_model=StrategyPublishResponse,
    status_code=status.HTTP_201_CREATED,
)
async def publish_strategy(
    strategy_id: UUID,
    request: StrategyPublishRequest,
    store: Annotated[StrategyPublicationStore, Depends(get_strategy_publication_store)],
) -> StrategyPublishResponse:
    """Validate and persist one browser draft as an immutable research artifact."""
    draft = request.strategy
    if draft.strategy_id != strategy_id or draft.status is not StrategyStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only the matching durable draft can be published.",
        )
    try:
        published = await store.publish_draft(draft, expected_revision=request.revision)
    except StrategyPublicationError as error:
        if str(error) == "Strategy draft was not found.":
            status_code = status.HTTP_404_NOT_FOUND
            detail = "Strategy draft was not found."
        elif str(error) == "Strategy draft revision conflict.":
            status_code = status.HTTP_409_CONFLICT
            detail = "Strategy draft changed; reload before publishing."
        else:
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            detail = "Strategy publication is unavailable."
        raise HTTPException(status_code=status_code, detail=detail) from None
    published_definition = StrategyDefinition.model_validate(
        {**draft.model_dump(mode="python"), "status": StrategyStatus.PUBLISHED}
    )
    _require_exact_publication(published, published_definition)
    return StrategyPublishResponse(
        strategy_fingerprint=published.strategy_fingerprint,
        strategy=published.definition,
    )


def _require_exact_publication(
    published: PublishedStrategy,
    expected: StrategyDefinition,
) -> None:
    """Reject a storage boundary that returns mismatched immutable strategy evidence."""
    if published.definition != expected or published.strategy_fingerprint != strategy_fingerprint(
        expected
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy publication is unavailable.",
        )


def _require_exact_draft(
    draft: StrategyDraft,
    *,
    detail: str,
    expected: StrategyDefinition | None = None,
    expected_revision: int | None = None,
) -> StrategyDraft:
    """Revalidate draft-store output and bind expected content and revision identity."""
    try:
        definition = StrategyDefinition.model_validate(draft.definition.model_dump(mode="python"))
        revision = draft.revision
    except AttributeError, TypeError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail
        ) from None
    if (
        definition.status is not StrategyStatus.DRAFT
        or type(revision) is not int
        or revision < 1
        or (expected is not None and definition != expected)
        or (expected_revision is not None and revision != expected_revision)
    ):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
    return StrategyDraft(definition=definition, revision=revision)


def _require_exact_catalog_entry(
    entry: StrategyCatalogEntry,
    *,
    expected_fingerprint: str | None = None,
    require_archive_marker: bool = False,
) -> StrategyDefinition:
    """Revalidate catalog evidence and bind it to canonical and requested identity."""
    try:
        definition = StrategyDefinition.model_validate(entry.definition.model_dump(mode="python"))
        canonical_fingerprint = strategy_fingerprint(definition)
    except TypeError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy publication catalog is unavailable.",
        ) from None
    archived_at = entry.archived_at
    archive_marker_invalid = archived_at is not None and (
        not isinstance(archived_at, datetime) or archived_at.utcoffset() != timedelta(0)
    )
    if (
        definition.status is not StrategyStatus.PUBLISHED
        or entry.strategy_fingerprint != canonical_fingerprint
        or (expected_fingerprint is not None and entry.strategy_fingerprint != expected_fingerprint)
        or archive_marker_invalid
        or (require_archive_marker and archived_at is None)
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy publication catalog is unavailable.",
        )
    return definition


def _strategy_summary(definition: StrategyDefinition) -> str:
    """Render a bounded human-readable outline from validated strategy semantics."""
    rsi = next(
        (indicator for indicator in definition.indicators if indicator.kind is IndicatorKind.RSI),
        None,
    )
    ema_summary = _ema_crossover_summary(definition)
    rsi_summary = _rsi_filter_summary(definition) if rsi is not None else "No RSI filter"
    risk_text = _shift_decimal_text(definition.sizing.risk_fraction, places=2)
    return (
        f"{definition.instrument.product_id} · {definition.timeframe} · {ema_summary} · "
        f"{rsi_summary} · {risk_text}% risk · "
        f"${definition.sizing.min_quote_notional}-${definition.sizing.max_quote_notional}"
    )


def _shift_decimal_text(value: str, *, places: int) -> str:
    """Shift an exact canonical decimal point without ambient-context arithmetic."""
    sign, digits, exponent = Decimal(value).as_tuple()
    if not isinstance(exponent, int):
        raise TypeError("Strategy summary requires a finite decimal risk fraction.")
    digit_text = "".join(str(digit) for digit in digits) or "0"
    shifted_exponent = exponent + places
    if shifted_exponent >= 0:
        result = digit_text + ("0" * shifted_exponent)
    else:
        point = len(digit_text) + shifted_exponent
        if point <= 0:
            result = f"0.{('0' * -point)}{digit_text}"
        else:
            result = f"{digit_text[:point]}.{digit_text[point:]}"
    result = result.rstrip("0").rstrip(".") if "." in result else result
    unsigned = result or "0"
    return f"-{unsigned}" if sign else unsigned


def _ema_crossover_summary(definition: StrategyDefinition) -> str:
    """Describe the first EMA crossover from its validated condition operands."""
    indicators = {indicator.id: indicator for indicator in definition.indicators}
    for condition in _comparison_leaves(definition.entry.when):
        if condition.operator not in {
            ComparisonOperator.CROSSES_ABOVE,
            ComparisonOperator.CROSSES_BELOW,
        }:
            continue
        if not isinstance(condition.left, IndicatorOperand) or not isinstance(
            condition.right, IndicatorOperand
        ):
            continue
        left = indicators[condition.left.indicator]
        right = indicators[condition.right.indicator]
        if left.kind is not IndicatorKind.EMA or right.kind is not IndicatorKind.EMA:
            continue
        direction = "above" if condition.operator is ComparisonOperator.CROSSES_ABOVE else "below"
        return f"EMA({left.parameters.period}) crosses {direction} EMA({right.parameters.period})"
    return "EMA rules"


def _comparison_leaves(
    condition: ComparisonCondition | AllCondition | AnyCondition | NotCondition,
) -> tuple[ComparisonCondition, ...]:
    """Flatten a bounded validated condition tree for semantic rendering."""
    if isinstance(condition, ComparisonCondition):
        return (condition,)
    if isinstance(condition, NotCondition):
        return _comparison_leaves(condition.not_)
    children = condition.all if isinstance(condition, AllCondition) else condition.any
    return tuple(leaf for child in children for leaf in _comparison_leaves(child))


def _rsi_filter_summary(definition: StrategyDefinition) -> str:
    """Describe the first top-level RSI threshold when the reference profile has one."""
    conditions = (
        definition.entry.when.all if isinstance(definition.entry.when, AllCondition) else ()
    )
    symbols = {
        ComparisonOperator.GT: ">",
        ComparisonOperator.GTE: "≥",
        ComparisonOperator.LT: "<",
        ComparisonOperator.LTE: "≤",
        ComparisonOperator.EQ: "=",
    }
    for condition in conditions:
        if not isinstance(condition, ComparisonCondition):
            continue
        if not isinstance(condition.left, IndicatorOperand) or not isinstance(
            condition.right, LiteralOperand
        ):
            continue
        if condition.left.indicator != "rsi" or condition.operator not in symbols:
            continue
        return f"RSI {symbols[condition.operator]} {condition.right.literal}"
    return "RSI filter"
