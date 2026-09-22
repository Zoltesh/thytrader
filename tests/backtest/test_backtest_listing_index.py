"""The unfiltered newest-first backtest listing must be index-backed.

``PostgresBacktestResultStore.list_summaries`` orders unfiltered discovery by
``(published_at DESC, result_fingerprint ASC)``. Without an index leading with
that pair, every unfiltered page scan/sorts the whole result table (whose rows
carry the large canonical result document) before ``LIMIT`` applies, so a
one-row request costs as much as a full page.
"""

from __future__ import annotations

from pathlib import Path

from thytrader.persistence.schema import metadata

_INDEX_NAME = "ix_published_backtest_results_published_result"


def _migration_tree_text() -> str:
    """Concatenate every Alembic migration file's source text."""
    versions = Path("alembic/versions")
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(versions.glob("*.py")))


def test_result_listing_has_a_newest_first_ordering_index() -> None:
    """Schema metadata must carry an index leading with the listing's sort key."""
    table = metadata.tables["published_backtest_results"]
    expected_prefix = (
        table.c.published_at,
        table.c.result_fingerprint,
    )
    matching = [index for index in table.indexes if tuple(index.columns)[:2] == expected_prefix]
    assert matching, "no index leads with (published_at, result_fingerprint)"


def test_newest_first_listing_index_is_declared_in_migrations() -> None:
    """The ordering index must exist in the applied Alembic chain, not only metadata."""
    assert _INDEX_NAME in _migration_tree_text()
