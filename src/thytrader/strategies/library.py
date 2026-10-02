"""Mutable strategy objects: documents, validation, deletion outcomes, and the store contract.

A strategy is one mutable object the user edits and saves (ADR 0082). Saving a
work-in-progress document that does not validate is allowed; the row stores the
document with its validation result. Starting a backtest, study, or deployment
requires a currently valid document and snapshots it by content fingerprint
(:mod:`thytrader.strategies.snapshots`). Concurrent saves are guarded by a
revision counter; a stale save is rejected, never merged or overwritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
import re
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydantic import JsonValue, TypeAdapter, ValidationError

from thytrader.market_data.models import EXECUTION_TIMEFRAMES
from thytrader.market_data.products import SPOT_PRODUCT_ID_PATTERN
from thytrader.strategies.issue_paths import document_issues
from thytrader.strategies.models import (
    LEGACY_LIFECYCLE_KEYS,
    StrategyDefinition,
    canonical_strategy_bytes,
    strategy_fingerprint,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.strategies.snapshots import StrategySnapshot

StrategyDocument = dict[str, JsonValue]
"""One JSON object as authored; valid or not."""

MAX_DOCUMENT_BYTES = 256 * 1024
MAX_VALIDATION_ISSUES = 50
MAX_BULK_DELETE = 100
DEFAULT_STRATEGY_NAME = "Untitled strategy"
_PRODUCT_PATTERN = re.compile(SPOT_PRODUCT_ID_PATTERN)
_DOCUMENT_ADAPTER: TypeAdapter[StrategyDocument] = TypeAdapter(StrategyDocument)


class StrategyLibraryError(RuntimeError):
    """Base class for redacted mutable-strategy failures."""


class StrategyStorageUnavailableError(StrategyLibraryError):
    """Durable strategy storage could not complete the request."""


class StrategyNotFoundError(StrategyLibraryError):
    """No strategy row exists for the requested identity."""


class StrategySnapshotNotFoundError(StrategyLibraryError):
    """No snapshot exists for the requested fingerprint."""


class StrategyDocumentError(StrategyLibraryError):
    """The submitted document is not a bounded JSON object."""


class StrategyRevisionConflictError(StrategyLibraryError):
    """A save named a revision that is no longer current."""

    def __init__(self, current_revision: int) -> None:
        """Record the durable revision the caller must reload."""
        super().__init__("Strategy was changed by another save; reload before saving.")
        self.current_revision = current_revision


class StrategyInvalidError(StrategyLibraryError):
    """The current definition does not validate, so nothing may start from it."""

    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        """Keep the issues so callers can explain why starts are blocked."""
        count = len(issues)
        super().__init__(
            f"Strategy definition is invalid ({count} issue{'s' if count != 1 else ''}); "
            "fix and save it before starting a backtest, study, or deployment."
        )
        self.issues = issues


class StrategyDeletionBlockedError(StrategyLibraryError):
    """Deletion is refused while any deployment of the strategy is running or paused."""

    def __init__(self, deployment_ids: tuple[UUID, ...]) -> None:
        """Name the deployments that must be stopped first."""
        super().__init__("Stop the strategy's running or paused deployments before deleting it.")
        self.deployment_ids = deployment_ids


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One schema or semantic problem at a dotted document location."""

    loc: str
    message: str


@dataclass(frozen=True, slots=True)
class StrategyValidation:
    """The validation result stored alongside a saved document."""

    issues: tuple[ValidationIssue, ...] = ()

    @property
    def valid(self) -> bool:
        """True when the document is a complete, valid strategy definition."""
        return not self.issues


@dataclass(frozen=True, slots=True)
class EvaluatedDocument:
    """A normalized document plus everything derived from validating it."""

    document: StrategyDocument
    stored_text: str
    definition: StrategyDefinition | None
    validation: StrategyValidation
    fingerprint: str | None
    name: str
    product_id: str | None
    timeframe: str | None


@dataclass(frozen=True, slots=True)
class StrategyRecord:
    """One persisted mutable strategy as the API and CLIs present it."""

    strategy_id: UUID
    name: str
    revision: int
    created_at: datetime
    updated_at: datetime
    document: StrategyDocument
    definition: StrategyDefinition | None
    validation: StrategyValidation
    current_fingerprint: str | None
    product_id: str | None
    timeframe: str | None


RESEARCH_TAG = "claude-research"
"""Tag agent research runs put in ``metadata.tags`` on the strategies they create."""
RESEARCH_TAG_PREFIX = "research-"
"""Any ``research-*`` tag (for example ``research-market-variant``) also marks research."""


class StrategyOrigin(StrEnum):
    """Who a library row belongs to, read from its ``metadata.tags``.

    ``research`` rows carry ``claude-research`` or a ``research-*`` tag; ``operator`` rows
    carry neither. ``all`` applies no origin filter.
    """

    ALL = "all"
    OPERATOR = "operator"
    RESEARCH = "research"


def is_research_tag(tag: str) -> bool:
    """Whether one tag marks agent research (``claude-research`` or ``research-*``)."""
    return tag == RESEARCH_TAG or tag.startswith(RESEARCH_TAG_PREFIX)


def document_origin(document: StrategyDocument) -> StrategyOrigin:
    """Classify a stored document as research or operator by its tags."""
    if any(is_research_tag(tag) for tag in document_tags(document)):
        return StrategyOrigin.RESEARCH
    return StrategyOrigin.OPERATOR


def matches_origin(document: StrategyDocument, origin: StrategyOrigin) -> bool:
    """Whether a stored document belongs in a library view filtered by ``origin``."""
    return origin is StrategyOrigin.ALL or document_origin(document) is origin


@dataclass(frozen=True, slots=True)
class StrategyPage:
    """One newest-updated-first page of strategies plus the library total."""

    records: tuple[StrategyRecord, ...]
    total: int


@dataclass(frozen=True, slots=True)
class SnapshotLookup:
    """One verified snapshot with its owning strategy (None once the owner is deleted)."""

    snapshot: StrategySnapshot
    strategy_id: UUID | None
    strategy_name: str | None
    created_at: datetime
    is_current: bool


@dataclass(frozen=True, slots=True)
class StrategyDeletionCounts:
    """What a strategy deletion removes (or, for kept live books, detaches)."""

    snapshots: int = 0
    backtests: int = 0
    research_runs: int = 0
    studies: int = 0
    research_jobs: int = 0
    dataset_bindings: int = 0
    paper_deployments: int = 0
    live_deployments_kept: int = 0
    allocations_removed: int = 0
    portfolio_sleeves: int = 0
    """Sleeves removed from portfolios (each journaled as ``sleeve_removed``; ADR 0088)."""


@dataclass(frozen=True, slots=True)
class StrategyDeletionPreview:
    """A dry-run of deleting one strategy, including any blocking deployments."""

    strategy_id: UUID
    name: str
    counts: StrategyDeletionCounts
    blocking_deployment_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class StrategyDeletionResult:
    """The committed outcome of deleting one strategy."""

    strategy_id: UUID
    name: str
    counts: StrategyDeletionCounts
    risk_policy_republished: bool


BulkOutcome = Literal["deleted", "would_delete", "blocked", "not_found", "failed"]


@dataclass(frozen=True, slots=True)
class BulkDeletionItem:
    """One strategy's outcome inside a bulk delete (or dry run)."""

    strategy_id: UUID
    outcome: BulkOutcome
    name: str | None = None
    code: str | None = None
    message: str | None = None
    deployment_ids: tuple[UUID, ...] = ()
    counts: StrategyDeletionCounts | None = None
    risk_policy_republished: bool = False


@dataclass(frozen=True, slots=True)
class BulkDeletionReport:
    """Per-strategy results for a bulk delete; partial failure is normal."""

    dry_run: bool
    items: tuple[BulkDeletionItem, ...] = field(default=())

    def count(self, outcome: BulkOutcome) -> int:
        """Return how many items ended with ``outcome``."""
        return sum(1 for item in self.items if item.outcome == outcome)


@runtime_checkable
class StrategyStore(Protocol):
    """Persist mutable strategies, their snapshots, and cascading deletion."""

    async def create(
        self, document: StrategyDocument, *, strategy_id: UUID, created_at: datetime
    ) -> StrategyRecord:
        """Insert one new strategy at revision 1 (valid or not)."""
        ...

    async def get(self, strategy_id: UUID) -> StrategyRecord:
        """Load one strategy or raise :class:`StrategyNotFoundError`."""
        ...

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        tag: str | None = None,
        origin: StrategyOrigin = StrategyOrigin.ALL,
    ) -> StrategyPage:
        """Return one newest-updated-first page and the total count.

        ``tag`` keeps only strategies whose document lists it in ``metadata.tags``
        (ADR 0094); ``origin`` keeps only research or only operator strategies (ADR 0098).
        ``total`` then counts the matches of both filters.
        """
        ...

    async def save(
        self, strategy_id: UUID, document: StrategyDocument, *, expected_revision: int
    ) -> StrategyRecord:
        """Replace the document only when ``expected_revision`` is current."""
        ...

    async def snapshot(self, strategy_id: UUID) -> StrategySnapshot:
        """Snapshot the current valid definition (deduplicated by fingerprint)."""
        ...

    async def lookup_snapshot(self, strategy_fingerprint_value: str) -> SnapshotLookup:
        """Load one snapshot plus its owner, or raise :class:`StrategySnapshotNotFoundError`."""
        ...

    async def preview_deletion(self, strategy_id: UUID) -> StrategyDeletionPreview:
        """Count what deleting the strategy would remove, without changing anything."""
        ...

    async def delete(self, strategy_id: UUID) -> StrategyDeletionResult:
        """Hard-delete the strategy and cascade (live books are detached, not deleted)."""
        ...


class DisabledStrategyStore:
    """Fail closed when durable strategy storage is not configured."""

    async def create(
        self, document: StrategyDocument, *, strategy_id: UUID, created_at: datetime
    ) -> StrategyRecord:
        """Refuse creation without durable storage."""
        del document, strategy_id, created_at
        raise StrategyStorageUnavailableError("Strategy storage is unavailable.")

    async def get(self, strategy_id: UUID) -> StrategyRecord:
        """Refuse reads without durable storage."""
        del strategy_id
        raise StrategyStorageUnavailableError("Strategy storage is unavailable.")

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        tag: str | None = None,
        origin: StrategyOrigin = StrategyOrigin.ALL,
    ) -> StrategyPage:
        """Refuse listing without durable storage."""
        del limit, offset, tag, origin
        raise StrategyStorageUnavailableError("Strategy storage is unavailable.")

    async def save(
        self, strategy_id: UUID, document: StrategyDocument, *, expected_revision: int
    ) -> StrategyRecord:
        """Refuse saves without durable storage."""
        del strategy_id, document, expected_revision
        raise StrategyStorageUnavailableError("Strategy storage is unavailable.")

    async def snapshot(self, strategy_id: UUID) -> StrategySnapshot:
        """Refuse snapshots without durable storage."""
        del strategy_id
        raise StrategyStorageUnavailableError("Strategy storage is unavailable.")

    async def lookup_snapshot(self, strategy_fingerprint_value: str) -> SnapshotLookup:
        """Refuse snapshot reads without durable storage."""
        del strategy_fingerprint_value
        raise StrategyStorageUnavailableError("Strategy storage is unavailable.")

    async def preview_deletion(self, strategy_id: UUID) -> StrategyDeletionPreview:
        """Refuse deletion previews without durable storage."""
        del strategy_id
        raise StrategyStorageUnavailableError("Strategy storage is unavailable.")

    async def delete(self, strategy_id: UUID) -> StrategyDeletionResult:
        """Refuse deletion without durable storage."""
        del strategy_id
        raise StrategyStorageUnavailableError("Strategy storage is unavailable.")


def parse_document(value: object) -> StrategyDocument:
    """Validate an untrusted value as one bounded JSON object document."""
    try:
        document = _DOCUMENT_ADAPTER.validate_python(value, strict=True)
    except ValidationError as error:
        raise StrategyDocumentError("Strategy document must be a JSON object.") from error
    if len(_sorted_json(document).encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise StrategyDocumentError("Strategy document exceeds the 256 KiB limit.")
    return document


def parse_document_text(text: str) -> StrategyDocument:
    """Decode stored document JSON back into a document object."""
    return parse_document(json.loads(text))


def evaluate_document(
    document: StrategyDocument,
    *,
    strategy_id: UUID,
    created_at: datetime,
    fallback_name: str = DEFAULT_STRATEGY_NAME,
) -> EvaluatedDocument:
    """Force row identity, drop legacy lifecycle keys, and validate one document.

    Valid documents are stored in canonical form, so their stored text is exactly
    the bytes a snapshot fingerprints. Invalid documents are stored as sorted JSON.
    """
    normalized: StrategyDocument = {
        key: value for key, value in document.items() if key not in LEGACY_LIFECYCLE_KEYS
    }
    normalized["strategy_id"] = str(strategy_id)
    normalized["created_at"] = created_at.isoformat().replace("+00:00", "Z")
    try:
        definition = StrategyDefinition.model_validate(normalized)
    except ValidationError as error:
        return _invalid_evaluation(normalized, error, fallback_name)
    retired = authoring_issues(definition)
    if retired:
        return _issue_evaluation(normalized, retired, fallback_name)
    canonical = canonical_strategy_bytes(definition).decode("utf-8")
    return EvaluatedDocument(
        document=parse_document_text(canonical),
        stored_text=canonical,
        definition=definition,
        validation=StrategyValidation(),
        fingerprint=strategy_fingerprint(definition),
        name=definition.name,
        product_id=definition.instrument.product_id,
        timeframe=definition.timeframe,
    )


RETIRED_ENTRY_PREFERENCE_MESSAGE = (
    "entry_preference 'marketable_limit' is not supported: backtest, paper, and live entries "
    "are always post-only maker limits. Use 'maker_only'."
)


def authoring_issues(definition: StrategyDefinition) -> tuple[ValidationIssue, ...]:
    """Return issues for schema values kept only so stored snapshots still verify.

    ``execution.entry_preference = "marketable_limit"`` was accepted but never honored
    (every runtime rested post-only maker entries). Persisted snapshots that carry it still
    parse byte-for-byte, but a document using it cannot be saved valid, backtested, or
    deployed.
    """
    if definition.execution.entry_preference == "marketable_limit":
        return (ValidationIssue("execution.entry_preference", RETIRED_ENTRY_PREFERENCE_MESSAGE),)
    return ()


def _invalid_evaluation(
    document: StrategyDocument, error: ValidationError, fallback_name: str
) -> EvaluatedDocument:
    """Describe one invalid document without trusting any of its fields.

    Issue locations are document paths (``entry.when.all[0].left.input``), never
    Pydantic union-member tags, and messages are plain language (ADR 0094).
    """
    issues = tuple(
        ValidationIssue(loc=item.loc, message=item.message)
        for item in document_issues(error, document, limit=MAX_VALIDATION_ISSUES)
    )
    return _issue_evaluation(document, issues, fallback_name)


def _issue_evaluation(
    document: StrategyDocument, issues: tuple[ValidationIssue, ...], fallback_name: str
) -> EvaluatedDocument:
    """Store one document as invalid with the given issues."""
    return EvaluatedDocument(
        document=document,
        stored_text=_sorted_json(document),
        definition=None,
        validation=StrategyValidation(issues=issues or (ValidationIssue("(document)", "invalid"),)),
        fingerprint=None,
        name=_document_name(document, fallback_name),
        product_id=_document_product(document),
        timeframe=_document_timeframe(document),
    )


def document_tags(document: StrategyDocument) -> tuple[str, ...]:
    """Return a stored document's ``metadata.tags`` (valid or work in progress).

    Malformed metadata in an invalid draft yields no tags rather than an error.
    """
    metadata = document.get("metadata")
    tags = metadata.get("tags") if isinstance(metadata, dict) else None
    if not isinstance(tags, list):
        return ()
    return tuple(item for item in tags if isinstance(item, str))


def clone_document(record: StrategyRecord, *, name: str | None = None) -> StrategyDocument:
    """Copy one strategy's document for a new identity.

    ``name`` becomes the copy's name (ADR 0094); without it the copy is marked
    ``<name> (copy)``.
    """
    copied: StrategyDocument = dict(record.document)
    copied["name"] = (name if name is not None else f"{record.name} (copy)")[:120]
    return copied


def validation_issues_json(validation: StrategyValidation) -> str:
    """Serialize stored validation issues as a compact JSON array."""
    return json.dumps(
        [{"loc": issue.loc, "message": issue.message} for issue in validation.issues],
        separators=(",", ":"),
        ensure_ascii=False,
    )


def validation_from_json(text: str) -> StrategyValidation:
    """Parse stored validation issues, failing closed on malformed rows."""
    loaded: object = json.loads(text)
    if not isinstance(loaded, list):
        raise TypeError("Stored validation issues must be a JSON array.")
    issues: list[ValidationIssue] = []
    for item in loaded:
        if not isinstance(item, dict):
            raise TypeError("Stored validation issue must be a JSON object.")
        loc, message = item.get("loc"), item.get("message")
        if not isinstance(loc, str) or not isinstance(message, str):
            raise TypeError("Stored validation issue fields must be strings.")
        issues.append(ValidationIssue(loc=loc, message=message))
    return StrategyValidation(issues=tuple(issues))


def _sorted_json(document: StrategyDocument) -> str:
    """Render a document deterministically for storage and size checks."""
    try:
        return json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except ValueError as error:
        raise StrategyDocumentError("Strategy document must be finite JSON.") from error


def _document_name(document: StrategyDocument, fallback: str) -> str:
    """Use the document name when it is a usable string."""
    name = document.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()[:120]
    return fallback


def _document_product(document: StrategyDocument) -> str | None:
    """Read the primary product id when it is well formed."""
    instrument = document.get("instrument")
    if not isinstance(instrument, dict):
        return None
    product_id = instrument.get("product_id")
    if isinstance(product_id, str) and _PRODUCT_PATTERN.fullmatch(product_id):
        return product_id
    return None


def _document_timeframe(document: StrategyDocument) -> str | None:
    """Read the decision timeframe when it is a supported clock."""
    timeframe = document.get("timeframe")
    if isinstance(timeframe, str) and timeframe in EXECUTION_TIMEFRAMES:
        return timeframe
    return None


async def create_strategy_from_definition(
    store: StrategyStore, definition: StrategyDefinition
) -> StrategyRecord:
    """Persist one server-built (template) definition as a new strategy."""
    document = parse_document_text(canonical_strategy_bytes(definition).decode("utf-8"))
    return await store.create(
        document, strategy_id=definition.strategy_id, created_at=definition.created_at
    )


async def import_strategy(
    store: StrategyStore,
    document: StrategyDocument,
    *,
    strategy_id: UUID,
    created_at: datetime,
) -> StrategyRecord:
    """Create a new strategy from an imported document under a fresh identity."""
    return await store.create(document, strategy_id=strategy_id, created_at=created_at)


async def clone_strategy(
    store: StrategyStore,
    source_id: UUID,
    *,
    strategy_id: UUID,
    created_at: datetime,
    name: str | None = None,
) -> StrategyRecord:
    """Duplicate one strategy's current document into a new identity, optionally renamed."""
    source = await store.get(source_id)
    return await store.create(
        clone_document(source, name=name), strategy_id=strategy_id, created_at=created_at
    )


async def bulk_delete_strategies(
    store: StrategyStore, strategy_ids: tuple[UUID, ...], *, dry_run: bool
) -> BulkDeletionReport:
    """Delete (or preview deleting) each strategy independently, reporting every outcome."""
    items = [await _bulk_item(store, strategy_id, dry_run=dry_run) for strategy_id in strategy_ids]
    return BulkDeletionReport(dry_run=dry_run, items=tuple(items))


async def _bulk_item(store: StrategyStore, strategy_id: UUID, *, dry_run: bool) -> BulkDeletionItem:
    """Resolve one bulk entry without letting one failure abort the others."""
    try:
        if dry_run:
            preview = await store.preview_deletion(strategy_id)
            if preview.blocking_deployment_ids:
                return _blocked_item(strategy_id, preview.name, preview.blocking_deployment_ids)
            return BulkDeletionItem(
                strategy_id=strategy_id,
                outcome="would_delete",
                name=preview.name,
                counts=preview.counts,
            )
        result = await store.delete(strategy_id)
    except StrategyNotFoundError:
        return BulkDeletionItem(
            strategy_id=strategy_id,
            outcome="not_found",
            code="strategy_not_found",
            message="Strategy was not found.",
        )
    except StrategyDeletionBlockedError as blocked:
        return _blocked_item(strategy_id, None, blocked.deployment_ids)
    except StrategyLibraryError:
        return BulkDeletionItem(
            strategy_id=strategy_id,
            outcome="failed",
            code="strategy_delete_failed",
            message="Strategy deletion is unavailable; nothing was changed for this strategy.",
        )
    return BulkDeletionItem(
        strategy_id=strategy_id,
        outcome="deleted",
        name=result.name,
        counts=result.counts,
        risk_policy_republished=result.risk_policy_republished,
    )


def _blocked_item(
    strategy_id: UUID, name: str | None, deployment_ids: tuple[UUID, ...]
) -> BulkDeletionItem:
    """Describe a strategy whose running or paused bots block deletion."""
    return BulkDeletionItem(
        strategy_id=strategy_id,
        outcome="blocked",
        name=name,
        code="strategy_has_active_deployments",
        message="Stop the strategy's running or paused bots before deleting it.",
        deployment_ids=deployment_ids,
    )
