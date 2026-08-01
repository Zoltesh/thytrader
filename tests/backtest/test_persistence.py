"""Contract tests for append-only immutable backtest-result persistence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, cast

from pydantic import SecretStr
import pytest
from sqlalchemy.dialects.postgresql import insert as postgres_insert

from thytrader.backtest.kernel import simulate_backtest
from thytrader.backtest.models import BacktestResult, backtest_result_fingerprint
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_backtests import (
    BacktestPublicationError,
    PostgresBacktestResultStore,
)
from thytrader.persistence.schema import (
    metadata,
    published_research_run_specs,
    published_strategy_versions,
    strategy_dataset_bindings,
)

from .test_kernel import _candles, _run, _strategy

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.strategies.models import StrategyDefinition


class _ExplodingEngine:
    """Fail if an invalid canonical result reaches a database transaction."""

    def begin(self) -> NoReturn:
        """Expose forbidden database access during fail-closed validation tests."""
        raise AssertionError("database access must not occur")


def test_schema_metadata_has_immutable_backtest_result_table() -> None:
    """Canonical results must be stored separately from run specifications and source artifacts."""
    table = metadata.tables["published_backtest_results"]

    assert set(table.primary_key.columns.keys()) == {"result_fingerprint"}
    assert "run_fingerprint" in table.columns
    assert not table.c.run_fingerprint.unique
    assert "strategy_fingerprint" in table.columns
    assert "dataset_fingerprint" in table.columns
    assert "signal_trace_fingerprint" in table.columns
    assert "canonical_result" in table.columns
    constraints = {constraint.name for constraint in table.constraints}
    assert "ck_backtest_result_fingerprint_format" in constraints
    assert "ck_backtest_result_run_fingerprint_format" in constraints


def test_backtest_result_migration_follows_research_run_publication() -> None:
    """The next migration must append results without rewriting immutable publication history."""
    content = Path("alembic/versions/0007_published_backtest_results.py").read_text(
        encoding="utf-8"
    )

    assert 'revision = "0007"' in content
    assert 'down_revision = "0006"' in content
    assert "published_backtest_results" in content
    assert "canonical_result" in content
    assert "ck_backtest_result_fingerprint_format" in content


def test_result_store_revalidates_forged_result_before_database_access() -> None:
    """Unchecked model copies cannot become durable result records."""
    strategy = _strategy()
    result = simulate_backtest(_run(strategy), strategy, _candles())
    forged_summary = result.summary.model_copy(update={"trade_count": -1})
    forged = result.model_copy(update={"summary": forged_summary})
    store = PostgresBacktestResultStore(cast("AsyncEngine", _ExplodingEngine()))

    with pytest.raises(BacktestPublicationError, match="invalid"):
        asyncio.run(store.publish(forged))


def test_postgres_store_persists_and_reloads_a_canonical_result() -> None:
    """A real PostgreSQL round trip keeps a canonical result byte-identical and idempotent."""
    database_url = os.environ.get("THYTRADER_INTEGRATION_DATABASE_URL")
    if database_url is None:
        pytest.skip("THYTRADER_INTEGRATION_DATABASE_URL is not configured")
    strategy = _strategy()
    result = simulate_backtest(_run(strategy), strategy, _candles())
    asyncio.run(_assert_postgres_round_trip(database_url, result, strategy))


async def _assert_postgres_round_trip(
    database_url: str,
    result: BacktestResult,
    strategy: StrategyDefinition,
) -> None:
    """Seed immutable source rows, append a result twice, and assert one verified load."""
    engine = create_engine(SecretStr(database_url))
    now = datetime.now(UTC)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                postgres_insert(published_strategy_versions)
                .values(
                    strategy_fingerprint=result.strategy_fingerprint,
                    strategy_id=str(strategy.strategy_id),
                    version=strategy.version,
                    created_at=strategy.created_at,
                    canonical_definition="{}",
                    published_at=now,
                )
                .on_conflict_do_nothing()
            )
            await connection.execute(
                postgres_insert(strategy_dataset_bindings)
                .values(
                    strategy_fingerprint=result.strategy_fingerprint,
                    dataset_fingerprint=result.dataset_fingerprint,
                    bound_at=now,
                )
                .on_conflict_do_nothing()
            )
            await connection.execute(
                postgres_insert(published_research_run_specs)
                .values(
                    run_fingerprint=result.run_fingerprint,
                    run_id="019cae99-3e01-7000-8000-000000000001",
                    created_at=now,
                    strategy_fingerprint=result.strategy_fingerprint,
                    dataset_fingerprint=result.dataset_fingerprint,
                    canonical_specification="{}",
                    published_at=now,
                )
                .on_conflict_do_nothing()
            )
        store = PostgresBacktestResultStore(engine)
        published = await store.publish(result)
        republished = await store.publish(result)
        reloaded = await store.load(backtest_result_fingerprint(result))
        assert published == result
        assert republished == result
        assert reloaded == result
    finally:
        await dispose(engine)
