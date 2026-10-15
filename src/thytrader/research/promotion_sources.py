"""Load the stored research a strategy fingerprint's promotion evidence is built from."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from thytrader.research.catalog import (
    StudyCatalogIntegrityError,
    StudyCatalogNotFoundError,
    StudyCatalogUnavailableError,
)
from thytrader.research.studies import ResearchStudy

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.research.catalog import ResearchStudyCatalog, StudyCatalogSummary

STUDY_SCAN_LIMIT = 100
"""How many of the newest catalog studies are searched for a fingerprint."""

STUDY_PAGE = 100
MAX_STUDY_PAGES = 10
"""A strategy's studies are read in pages; more than this many pages is incomplete coverage."""

_UNREADABLE = (
    StudyCatalogUnavailableError,
    StudyCatalogNotFoundError,
    StudyCatalogIntegrityError,
    ValidationError,
)


async def studies_for_strategy(
    catalog: ResearchStudyCatalog, strategy_fingerprint: str
) -> tuple[ResearchStudy, ...] | None:
    """Newest catalog studies with a window of ``strategy_fingerprint``; None if unreadable.

    A study that cannot be loaded or validated is skipped; only an unavailable catalog listing
    returns None, so callers can tell "no studies" from "could not look".
    """
    try:
        rows = await catalog.list_summaries(limit=STUDY_SCAN_LIMIT, offset=0)
    except StudyCatalogUnavailableError, StudyCatalogIntegrityError:
        return None
    matched: list[ResearchStudy] = []
    for row in rows:
        try:
            study = ResearchStudy.model_validate_json(await catalog.load(row.study_fingerprint))
        except _UNREADABLE:
            continue
        if any(window.strategy_fingerprint == strategy_fingerprint for window in study.windows):
            matched.append(study)
    return tuple(matched)


async def every_study_of(
    catalog: ResearchStudyCatalog, *, strategy_fingerprint: str, strategy_id: UUID
) -> tuple[tuple[ResearchStudy, ...], bool]:
    """Every catalog study with a window of the fingerprint, and whether the read was complete.

    Reads the strategy's own studies page by page (a process-local catalog, which cannot
    filter by strategy, is read whole). A listing or study that cannot be read, or more pages
    than ``MAX_STUDY_PAGES``, makes the result incomplete: what was found is returned, but it
    is not the whole record. A study seen twice (the catalog grew between pages) is
    incomplete too, so no study is ever counted twice.
    """
    owner: UUID | None = strategy_id
    matched: list[ResearchStudy] = []
    seen: set[str] = set()
    for page in range(MAX_STUDY_PAGES):
        rows, owner = await _list_page(catalog, owner=owner, page=page)
        if rows is None:
            return tuple(matched), False
        names = [row.study_fingerprint for row in rows]
        if seen.intersection(names) or len(set(names)) < len(names):
            # A study published between pages shifted them: never count one twice.
            return tuple(matched), False
        seen.update(names)
        found, complete = await _matching(catalog, rows, strategy_fingerprint)
        matched.extend(found)
        if not complete:
            return tuple(matched), False
        if len(rows) < STUDY_PAGE:
            return tuple(matched), True
    return tuple(matched), False


async def _list_page(
    catalog: ResearchStudyCatalog, *, owner: UUID | None, page: int
) -> tuple[tuple[StudyCatalogSummary, ...] | None, UUID | None]:
    """One page of the strategy's studies, falling back to the whole process-local catalog."""
    try:
        rows = await catalog.list_summaries(
            strategy_id=owner, limit=STUDY_PAGE, offset=page * STUDY_PAGE
        )
    except StudyCatalogUnavailableError:
        if owner is None or page:
            return None, owner
        return await _whole_catalog_page(catalog), None
    except StudyCatalogIntegrityError:
        return None, owner
    return rows, owner


async def _matching(
    catalog: ResearchStudyCatalog,
    rows: tuple[StudyCatalogSummary, ...],
    strategy_fingerprint: str,
) -> tuple[list[ResearchStudy], bool]:
    """The listed studies with a window of the fingerprint, and whether each could be read."""
    matched: list[ResearchStudy] = []
    complete = True
    for row in rows:
        try:
            study = ResearchStudy.model_validate_json(await catalog.load(row.study_fingerprint))
        except _UNREADABLE:
            complete = False
            continue
        if any(window.strategy_fingerprint == strategy_fingerprint for window in study.windows):
            matched.append(study)
    return matched, complete


async def _whole_catalog_page(
    catalog: ResearchStudyCatalog,
) -> tuple[StudyCatalogSummary, ...] | None:
    """The first page of a catalog read without a strategy filter, or None if unreadable."""
    try:
        return await catalog.list_summaries(limit=STUDY_PAGE, offset=0)
    except StudyCatalogUnavailableError, StudyCatalogIntegrityError:
        return None
