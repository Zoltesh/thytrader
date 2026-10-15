"""Load the stored research a strategy fingerprint's promotion evidence is built from."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from thytrader.research.catalog import StudyCatalogNotFoundError, StudyCatalogUnavailableError
from thytrader.research.studies import ResearchStudy

if TYPE_CHECKING:
    from thytrader.research.catalog import ResearchStudyCatalog

STUDY_SCAN_LIMIT = 100
"""How many of the newest catalog studies are searched for a fingerprint."""


async def studies_for_strategy(
    catalog: ResearchStudyCatalog, strategy_fingerprint: str
) -> tuple[ResearchStudy, ...] | None:
    """Newest catalog studies with a window of ``strategy_fingerprint``; None if unreadable.

    A study that cannot be loaded or validated is skipped; only an unavailable catalog listing
    returns None, so callers can tell "no studies" from "could not look".
    """
    try:
        rows = await catalog.list_summaries(limit=STUDY_SCAN_LIMIT, offset=0)
    except StudyCatalogUnavailableError:
        return None
    matched: list[ResearchStudy] = []
    for row in rows:
        try:
            study = ResearchStudy.model_validate_json(await catalog.load(row.study_fingerprint))
        except StudyCatalogUnavailableError, StudyCatalogNotFoundError, ValidationError:
            continue
        if any(window.strategy_fingerprint == strategy_fingerprint for window in study.windows):
            matched.append(study)
    return tuple(matched)
