"""Live PostgreSQL test for the 0077 cross-market catalog correction (ADR 0044 honesty).

0077 clears the catalog's ``mean_oos_return_fraction`` of cross-market studies, which
counted full windows as out-of-sample before the ``full_window_*`` aliases existed. Other
kinds keep their value, and the canonical study documents are untouched.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.persistence.test_migration_0048_strategy_root import _alembic, scratch_database

__all__ = ["scratch_database"]

pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL migration coverage.",
)

_STRATEGY = "01a00000-0000-7000-8000-000000000077"
_STUDIES = {"cross_market": "sha256:" + "c" * 64, "walk_forward": "sha256:" + "d" * 64}


def _execute(
    database_url: str, statement: str, parameters: dict[str, object] | None = None
) -> list[tuple[object, ...]]:
    """Run one statement and return its rows (empty for writes)."""

    async def run() -> list[tuple[object, ...]]:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                result = await connection.execute(text(statement), parameters or {})
                return [tuple(row) for row in result] if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_0077_clears_only_the_cross_market_oos_mean(scratch_database: str) -> None:
    """Cross-market rows lose the mislabelled mean; walk-forward keeps its genuine one."""
    assert _alembic(scratch_database, "0076").returncode == 0
    _execute(
        scratch_database,
        "INSERT INTO strategies (strategy_id, name, document, is_valid, revision, created_at, "
        "updated_at) VALUES (:strategy, 'S', '{}', false, 1, now(), now())",
        {"strategy": _STRATEGY},
    )
    for kind, fingerprint in _STUDIES.items():
        _execute(
            scratch_database,
            "INSERT INTO published_research_studies (study_fingerprint, strategy_id, "
            "request_fingerprint, plan_fingerprint, kind, product_id, timeframe, window_count, "
            "mean_oos_return_fraction, canonical_study, published_at) VALUES "
            "(:fingerprint, :strategy, :fingerprint, :fingerprint, :kind, 'BTC-USD', '1h', 2, "
            "'0.02', :document, now())",
            {
                "fingerprint": fingerprint,
                "strategy": _STRATEGY,
                "kind": kind,
                "document": '{"canonical": true}',
            },
        )
    upgraded = _alembic(scratch_database, "0077")
    assert upgraded.returncode == 0, upgraded.stderr
    rows = {
        row[0]: row[1]
        for row in _execute(
            scratch_database,
            "SELECT kind, mean_oos_return_fraction FROM published_research_studies",
        )
    }
    assert rows == {"cross_market": None, "walk_forward": "0.02"}
    documents = _execute(
        scratch_database, "SELECT DISTINCT canonical_study FROM published_research_studies"
    )
    assert documents == [('{"canonical": true}',)]
