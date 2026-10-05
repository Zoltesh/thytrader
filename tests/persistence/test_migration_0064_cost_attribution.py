"""The additive attribution column upgrades and downgrades on isolated PostgreSQL."""

import os

import pytest

from tests.persistence.test_migration_0048_strategy_root import _alembic, scratch_database
from tests.persistence.test_migration_0052_lookback_ceilings import _downgrade
from tests.persistence.test_migration_0057_research_worker_pool import _columns

__all__ = ["scratch_database"]

pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL migration coverage.",
)


def test_cost_attribution_is_additive_and_reversible(scratch_database: str) -> None:
    """0064 adds regenerable metadata and preserves existing evidence columns on downgrade."""
    previous = _alembic(scratch_database, "0063")
    assert previous.returncode == 0, previous.stderr
    before = _columns(scratch_database, "published_backtest_results")
    upgraded = _alembic(scratch_database, "head")
    assert upgraded.returncode == 0, upgraded.stderr
    assert _columns(scratch_database, "published_backtest_results") == before | {
        "cost_attribution_json"
    }
    downgraded = _downgrade(scratch_database, "0063")
    assert downgraded.returncode == 0, downgraded.stderr
    assert _columns(scratch_database, "published_backtest_results") == before
    again = _alembic(scratch_database, "head")
    assert again.returncode == 0, again.stderr
