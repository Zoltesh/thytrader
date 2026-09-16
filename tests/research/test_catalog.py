"""Unit tests for the in-memory research-study catalog."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from thytrader.research.catalog import (
    InMemoryResearchStudyCatalog,
    StudyCatalogIntegrityError,
    StudyCatalogNotFoundError,
    StudyCatalogSummary,
)


def _summary(*, fingerprint: str, kind: str = "oos_holdout") -> StudyCatalogSummary:
    """Build one catalog row for persistence tests."""
    return StudyCatalogSummary(
        study_fingerprint=fingerprint,
        request_fingerprint="sha256:" + "b" * 64,
        kind=kind,
        engine_contract_version="thytrader-bar-backtest-v1",
        published_at=datetime(2026, 9, 16, tzinfo=UTC),
        product_id="BTC-USD",
        timeframe="1h",
        window_count=2,
        mean_oos_return_fraction="0.01",
        stitched_oos_available=False,
    )


def test_in_memory_catalog_lists_newest_first() -> None:
    """Newest published_at wins; fingerprints break remaining ties."""
    catalog = InMemoryResearchStudyCatalog()
    older = "sha256:" + "1" * 64
    newer = "sha256:" + "2" * 64
    first = _summary(fingerprint=older)
    second = first.model_copy(
        update={
            "study_fingerprint": newer,
            "published_at": datetime(2026, 9, 17, tzinfo=UTC),
            "kind": "parameter_sweep",
        }
    )

    async def _scenario() -> tuple[tuple[str, ...], tuple[str, ...]]:
        await catalog.persist(first, f'{{"study_fingerprint":"{older}"}}')
        await catalog.persist(second, f'{{"study_fingerprint":"{newer}"}}')
        rows = await catalog.list_summaries()
        filtered = await catalog.list_summaries(kind="parameter_sweep")
        return (
            tuple(row.study_fingerprint for row in rows),
            tuple(row.study_fingerprint for row in filtered),
        )

    listed, filtered = asyncio.run(_scenario())
    assert listed == (newer, older)
    assert filtered == (newer,)


def test_in_memory_catalog_rejects_conflicting_canonical_json() -> None:
    """Repeating a fingerprint with different bytes is an integrity failure."""
    catalog = InMemoryResearchStudyCatalog()
    fingerprint = "sha256:" + "c" * 64

    async def _scenario() -> None:
        await catalog.persist(_summary(fingerprint=fingerprint), '{"a":1}')
        with pytest.raises(StudyCatalogIntegrityError, match="integrity"):
            await catalog.persist(_summary(fingerprint=fingerprint), '{"a":2}')
        with pytest.raises(StudyCatalogNotFoundError, match="not found"):
            await catalog.load("sha256:" + "d" * 64)

    asyncio.run(_scenario())
