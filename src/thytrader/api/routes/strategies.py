"""Browser-facing strategy library, draft, and immutable-publication HTTP contracts."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves this annotation at runtime.

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from thytrader.api.dependencies import (
    get_backtest_result_store,
    get_strategy_draft_store,
    get_strategy_publication_catalog,
    get_strategy_publication_store,
)
from thytrader.backtest.models import BacktestSummary  # noqa: TC001 - Pydantic model field.
from thytrader.persistence.backtest_results import (
    BacktestResultReader,  # noqa: TC001 - FastAPI resolves this annotation at runtime.
)
from thytrader.strategies.authoring import (
    StrategyDraft,
    StrategyDraftStore,
    create_cloned_draft,
    create_reference_draft,
)
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

_LIBRARY_SUMMARY_LIMIT = 5


class StrategyLibraryBacktestResponse(BaseModel):
    """One immutable backtest summary bound to the listed strategy identity."""

    result_fingerprint: str
    published_at: str
    summary: BacktestSummary


class StrategyLibraryPaperLiveResponse(BaseModel):
    """Explicit no-authority deployment status until runtimes exist."""

    paper: str = "unavailable"
    live: str = "unavailable"


class StrategyLibraryEntryResponse(BaseModel):
    """One stable strategy identity with its newest lifecycle evidence."""

    strategy_id: str
    name: str
    product_id: str
    timeframe: str
    latest_version: int | None
    status: str
    latest_fingerprint: str | None
    archived: bool
    summary: str
    backtest: StrategyLibraryBacktestResponse | None
    paper_live: StrategyLibraryPaperLiveResponse
    created_at: str
    updated_at: str


class StrategyListResponse(BaseModel):
    """The complete strategy library, newest-first by activity."""

    strategies: tuple[StrategyLibraryEntryResponse, ...]


class StrategyCreatedResponse(BaseModel):
    """One created draft document plus its library row and latest sibling evidence."""

    strategy: StrategyDefinition
    revision: int = Field(ge=1)
    created: StrategyLibraryEntryResponse
    siblings: tuple[StrategyLibraryEntryResponse, ...]


class StrategyDraftResponse(BaseModel):
    """One server-identified strategy draft safe for browser editing."""

    strategy: StrategyDefinition
    revision: int = Field(ge=1)
    summary: str


class StrategyDraftVersionResponse(BaseModel):
    """One complete durable draft document with its optimistic-concurrency revision."""

    strategy: StrategyDefinition
    revision: int = Field(ge=1)


class StrategyDraftRequest(BaseModel):
    """One complete editable strategy draft supplied by the browser."""

    strategy: StrategyDefinition
    revision: Annotated[int, Field(strict=True, ge=1)]


class StrategyCloneRequest(BaseModel):
    """One published strategy identity selected for draft cloning."""

    strategy_fingerprint: str


class StrategyCloneResponse(BaseModel):
    """One cloned draft derived from an immutable published strategy."""

    strategy: StrategyDefinition
    revision: int = Field(ge=1)
    summary: str


class StrategyImportRequest(BaseModel):
    """One complete strategy definition supplied for durable draft import."""

    strategy: StrategyDefinition


class StrategyImportResponse(BaseModel):
    """One imported draft as durably persisted by the authoring boundary."""

    strategy: StrategyDefinition
    revision: int = Field(ge=1)
    summary: str


class StrategyPublishRequest(BaseModel):
    """One complete durable draft supplied for immutable publication."""

    strategy: StrategyDefinition
    revision: Annotated[int, Field(strict=True, ge=1)]


class StrategyPublishResponse(BaseModel):
    """One immutable strategy version returned after authoritative persistence."""

    strategy_fingerprint: str
    strategy: StrategyDefinition


class StrategyArchiveResponse(BaseModel):
    """One immutable publication that is now hidden from active selection."""

    strategy_fingerprint: str
    archived_at: str | None


class StrategyDefinitionSourceResponse(BaseModel):
    """One complete canonical strategy definition returned for cloning or import."""

    strategy: StrategyDefinition


@router.get("", response_model=StrategyListResponse)
async def list_strategies(
    draft_store: Annotated[StrategyDraftStore, Depends(get_strategy_draft_store)],
    publication_catalog: Annotated[
        StrategyPublicationCatalog, Depends(get_strategy_publication_catalog)
    ],
    result_store: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
) -> StrategyListResponse:
    """Return the strategy library grouped by stable identity with latest evidence."""
    try:
        drafts = await draft_store.list_drafts()
    except RuntimeError, TypeError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy lifecycle storage is unavailable.",
        ) from None
    try:
        publications = await publication_catalog.list_published(include_archived=True)
    except RuntimeError, TypeError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy publication catalog is unavailable.",
        ) from None

    groups: dict[str, _LibraryGroup] = {}
    for draft in drafts:
        validated = _require_exact_draft(
            draft,
            detail="Strategy lifecycle storage is unavailable.",
        )
        _register_draft(groups, validated)
    for entry in publications:
        definition = _require_exact_catalog_entry(entry)
        _register_publication(groups, entry, definition)

    entries: list[StrategyLibraryEntryResponse] = []
    for group in groups.values():
        backtest = await _latest_backtest(group.fingerprints, result_store)
        entries.append(_library_entry(group, backtest))
    entries.sort(key=_activity_instant, reverse=True)
    return StrategyListResponse(strategies=tuple(entries))


@router.post("", response_model=StrategyCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_strategy_draft(
    draft_store: Annotated[StrategyDraftStore, Depends(get_strategy_draft_store)],
    publication_catalog: Annotated[
        StrategyPublicationCatalog, Depends(get_strategy_publication_catalog)
    ],
    result_store: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
) -> StrategyCreatedResponse:
    """Create and durably save the conservative reference draft without trading authority."""
    definition = create_reference_draft()
    try:
        draft = await draft_store.create_draft(definition)
    except RuntimeError, TypeError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy draft storage is unavailable.",
        ) from None
    try:
        drafts = await draft_store.list_drafts()
    except RuntimeError, TypeError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy lifecycle storage is unavailable.",
        ) from None
    try:
        publications = await publication_catalog.list_published(include_archived=True)
    except RuntimeError, TypeError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy publication catalog is unavailable.",
        ) from None
    draft = _require_exact_draft(
        draft,
        expected=definition,
        expected_revision=1,
        detail="Strategy draft storage is unavailable.",
    )
    groups: dict[str, _LibraryGroup] = {}
    for stored in drafts:
        _register_draft(
            groups, _require_exact_draft(stored, detail="Strategy draft storage is unavailable.")
        )
    for entry in publications:
        _register_publication(groups, entry, _require_exact_catalog_entry(entry))
    created = _library_entry(groups[str(definition.strategy_id)], None)
    siblings: list[StrategyLibraryEntryResponse] = []
    for identity, group in groups.items():
        if identity == str(definition.strategy_id):
            continue
        backtest = await _latest_backtest(group.fingerprints, result_store)
        siblings.append(_library_entry(group, backtest))
    siblings.sort(key=_activity_instant, reverse=True)
    return StrategyCreatedResponse(
        strategy=draft.definition,
        revision=draft.revision,
        created=created,
        siblings=tuple(siblings),
    )


@router.get(
    "/{strategy_id}/versions/{version}",
    response_model=StrategyDraftVersionResponse,
)
async def get_strategy_draft_version(
    strategy_id: UUID,
    version: int,
    store: Annotated[StrategyDraftStore, Depends(get_strategy_draft_store)],
) -> StrategyDraftVersionResponse:
    """Return one complete durable draft document for browser editing."""
    try:
        drafts = await store.list_drafts()
    except RuntimeError, TypeError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy draft storage is unavailable.",
        ) from None
    for draft in drafts:
        if draft.definition.strategy_id == strategy_id and draft.definition.version == version:
            validated = _require_exact_draft(
                draft,
                detail="Strategy draft storage is unavailable.",
            )
            return StrategyDraftVersionResponse(
                strategy=validated.definition,
                revision=validated.revision,
            )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Strategy draft was not found.",
    )


@router.post("/clone", response_model=StrategyCloneResponse, status_code=status.HTTP_201_CREATED)
async def clone_strategy_draft(
    request: StrategyCloneRequest,
    catalog: Annotated[StrategyPublicationCatalog, Depends(get_strategy_publication_catalog)],
    draft_store: Annotated[StrategyDraftStore, Depends(get_strategy_draft_store)],
) -> StrategyCloneResponse:
    """Create one new draft identity from immutable published strategy evidence."""
    source = await _published_definition(catalog, request.strategy_fingerprint)
    cloned = _cloned_draft_definition(source)
    try:
        draft = await draft_store.create_draft(cloned)
    except RuntimeError, TypeError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy draft storage is unavailable.",
        ) from None
    draft = _require_exact_draft(
        draft,
        expected=cloned,
        expected_revision=1,
        detail="Strategy draft storage is unavailable.",
    )
    return StrategyCloneResponse(
        strategy=draft.definition,
        revision=draft.revision,
        summary=_strategy_summary(draft.definition),
    )


@router.get("/source/{strategy_fingerprint}", response_model=StrategyDefinitionSourceResponse)
async def get_strategy_definition_source(
    strategy_fingerprint: str,
    catalog: Annotated[StrategyPublicationCatalog, Depends(get_strategy_publication_catalog)],
) -> StrategyDefinitionSourceResponse:
    """Return one immutable canonical strategy definition for editor hydration."""
    source = await _published_definition(catalog, strategy_fingerprint)
    return StrategyDefinitionSourceResponse(strategy=source)


@router.post("/import", response_model=StrategyImportResponse, status_code=status.HTTP_201_CREATED)
async def import_strategy(
    request: StrategyImportRequest,
    draft_store: Annotated[StrategyDraftStore, Depends(get_strategy_draft_store)],
    catalog: Annotated[StrategyPublicationCatalog, Depends(get_strategy_publication_catalog)],
) -> StrategyImportResponse:
    """Persist one supplied validated definition as a new editable draft identity."""
    supplied = request.strategy.model_copy(update={"status": StrategyStatus.DRAFT})
    try:
        supplied = StrategyDefinition.model_validate(supplied.model_dump(mode="python"))
    except TypeError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Strategy import requires a valid strategy definition.",
        ) from None
    if supplied.status is not StrategyStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Strategy import requires a draft status.",
        )
    await _require_import_identity_available(draft_store, catalog, supplied)
    try:
        draft = await draft_store.create_draft(supplied)
    except RuntimeError, TypeError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy draft storage is unavailable.",
        ) from None
    draft = _require_exact_draft(
        draft,
        expected=supplied,
        expected_revision=1,
        detail="Strategy draft storage is unavailable.",
    )
    return StrategyImportResponse(
        strategy=draft.definition,
        revision=draft.revision,
        summary=_strategy_summary(draft.definition),
    )


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


class _LibraryGroup:
    """One stable strategy identity accumulating its drafts and publications."""

    __slots__ = (
        "activity",
        "archived",
        "created_at",
        "drafts",
        "fingerprints",
        "latest_fingerprint",
        "latest_version",
        "name",
        "product_id",
        "publications",
        "status",
        "timeframe",
        "updated_at",
    )

    def __init__(self, definition: StrategyDefinition) -> None:
        """Start one group from its first observed draft or publication."""
        self.name = definition.name
        self.product_id = definition.instrument.product_id
        self.timeframe = definition.timeframe
        self.latest_version: int | None = None
        self.latest_fingerprint: str | None = None
        self.status = StrategyStatus.DRAFT
        self.archived = False
        self.created_at: datetime | None = None
        self.updated_at: datetime | None = None
        self.activity: datetime | None = None
        self.drafts: deque[StrategyDefinition] = deque()
        self.publications: deque[StrategyDefinition] = deque()
        self.fingerprints: tuple[str, ...] = ()

    def _observe(self, definition: StrategyDefinition) -> None:
        """Track the group-wide name, market, and time envelope of one version."""
        self.name = definition.name
        self.product_id = definition.instrument.product_id
        self.timeframe = definition.timeframe
        if self.created_at is None or definition.created_at < self.created_at:
            self.created_at = definition.created_at
        if self.updated_at is None or definition.created_at > self.updated_at:
            self.updated_at = definition.created_at

    def observe_draft(self, definition: StrategyDefinition) -> None:
        """Record one editable draft version inside the stable identity group."""
        self._observe(definition)
        self.drafts.append(definition)
        if self.activity is None or definition.created_at > self.activity:
            self.activity = definition.created_at
        if self.latest_version is None or definition.version > self.latest_version:
            self.latest_version = definition.version
            self.status = StrategyStatus.DRAFT

    def observe_publication(
        self,
        entry: StrategyCatalogEntry,
        definition: StrategyDefinition,
    ) -> None:
        """Record one immutable version and its optional archive marker."""
        self._observe(definition)
        self.publications.append(definition)
        if self.activity is None or definition.created_at > self.activity:
            self.activity = definition.created_at
        if self.latest_version is None or definition.version > self.latest_version:
            self.latest_version = definition.version
            self.status = StrategyStatus.PUBLISHED
        self.latest_fingerprint = entry.strategy_fingerprint
        if entry.archived_at is not None:
            self.archived = True

    def require_times(self) -> tuple[datetime, datetime]:
        """Return the group envelope, rejecting identities without observed versions."""
        if self.created_at is None or self.updated_at is None:
            message = "Strategy library group has no observed versions."
            raise TypeError(message)
        return (self.created_at, self.updated_at)


async def _published_definition(
    catalog: StrategyPublicationCatalog,
    strategy_fingerprint_value: str,
) -> StrategyDefinition:
    """Resolve one immutable definition by fingerprint with strict identity binding."""
    try:
        publications = await catalog.list_published(include_archived=True)
    except RuntimeError, TypeError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy publication catalog is unavailable.",
        ) from None
    for entry in publications:
        if entry.strategy_fingerprint != strategy_fingerprint_value:
            continue
        definition = _require_exact_catalog_entry(
            entry,
            expected_fingerprint=strategy_fingerprint_value,
        )
        return definition  # noqa: RET504 - name keeps the revalidation result explicit.
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Published strategy was not found.",
    )


def _cloned_draft_definition(source: StrategyDefinition) -> StrategyDefinition:
    """Derive a fresh draft identity from immutable evidence without changing semantics."""
    return create_cloned_draft(source)


async def _require_import_identity_available(
    draft_store: StrategyDraftStore,
    catalog: StrategyPublicationCatalog,
    supplied: StrategyDefinition,
) -> None:
    """Reject imports that would overwrite an existing draft or equal a known publication."""
    identity = str(supplied.strategy_id)
    try:
        drafts = await draft_store.list_drafts()
        publications = await catalog.list_published(include_archived=True)
    except RuntimeError, TypeError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy lifecycle storage is unavailable.",
        ) from None
    for draft in drafts:
        if str(draft.definition.strategy_id) == identity:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A draft with this strategy identity already exists.",
            )
    for entry in publications:
        if entry.definition == supplied:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This strategy is already published; clone it instead.",
            )


def _register_draft(
    groups: dict[str, _LibraryGroup],
    draft: StrategyDraft,
) -> None:
    """Register one revalidated draft in its stable identity group."""
    identity = str(draft.definition.strategy_id)
    group = groups.setdefault(identity, _LibraryGroup(draft.definition))
    group.observe_draft(draft.definition)


def _register_publication(
    groups: dict[str, _LibraryGroup],
    entry: StrategyCatalogEntry,
    definition: StrategyDefinition,
) -> None:
    """Register one revalidated immutable publication in its identity group."""
    identity = str(definition.strategy_id)
    group = groups.setdefault(identity, _LibraryGroup(definition))
    group.observe_publication(entry, definition)
    fingerprints = list(group.fingerprints)
    fingerprints.append(entry.strategy_fingerprint)
    group.fingerprints = tuple(fingerprints)


async def _latest_backtest(
    fingerprints: tuple[str, ...],
    result_store: BacktestResultReader,
) -> StrategyLibraryBacktestResponse | None:
    """Resolve the newest immutable backtest bound to any version of one strategy."""
    for fingerprint_value in fingerprints:
        try:
            summaries = await result_store.list_summaries(
                strategy_fingerprint=fingerprint_value,
                limit=1,
                offset=0,
            )
        except Exception:  # noqa: BLE001, S112 - redacted per-request degradation.
            continue
        if not summaries:
            continue
        newest = summaries[0]
        return StrategyLibraryBacktestResponse(
            result_fingerprint=newest.result_fingerprint,
            published_at=newest.published_at.isoformat(),
            summary=newest.summary,
        )
    return None


def _library_entry(
    group: _LibraryGroup,
    backtest: StrategyLibraryBacktestResponse | None,
) -> StrategyLibraryEntryResponse:
    """Project one identity group into its bounded library row."""
    created_at, updated_at = group.require_times()
    if not group.drafts and group.latest_fingerprint is not None:
        group.status = StrategyStatus.PUBLISHED
    representative = group.drafts[0] if group.drafts else group.publications[0]
    return StrategyLibraryEntryResponse(
        strategy_id=representative.strategy_id
        if isinstance(representative.strategy_id, str)
        else str(representative.strategy_id),
        name=group.name,
        product_id=group.product_id,
        timeframe=group.timeframe,
        latest_version=group.latest_version,
        status=(
            "archived"
            if group.archived and group.status is not StrategyStatus.DRAFT
            else group.status.value
        ),
        latest_fingerprint=group.latest_fingerprint,
        archived=group.archived,
        summary=_strategy_summary(representative),
        backtest=backtest,
        paper_live=StrategyLibraryPaperLiveResponse(),
        created_at=created_at.isoformat(),
        updated_at=updated_at.isoformat(),
    )


def _activity_instant(entry: StrategyLibraryEntryResponse) -> datetime:
    """Parse one library row's activity instant for newest-first sorting."""
    return datetime.fromisoformat(entry.updated_at)


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
