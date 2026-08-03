"""Contract tests for append-only immutable backtest-result persistence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, cast

from pydantic import SecretStr
import pytest
from sqlalchemy import delete, select
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
    published_backtest_results,
    published_research_run_specs,
    published_strategy_versions,
    strategy_dataset_bindings,
)
from thytrader.research.models import ResearchRunSpecification, canonical_research_run_bytes
from thytrader.research.signal_evaluator import evaluate_signal_trace

from .test_kernel import _candles, _run, _strategy, _v2_run

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
        asyncio.run(
            store.publish(
                forged,
                trace=evaluate_signal_trace(_run(strategy), strategy, _candles()),
            )
        )


def test_postgres_store_persists_and_reloads_a_canonical_result() -> None:
    """A real PostgreSQL round trip keeps a canonical result byte-identical and idempotent."""
    database_url = os.environ.get("THYTRADER_INTEGRATION_DATABASE_URL")
    if database_url is None:
        pytest.skip("THYTRADER_INTEGRATION_DATABASE_URL is not configured")
    strategy = _strategy()
    specification = _run(strategy)
    result = simulate_backtest(specification, strategy, _candles())
    asyncio.run(_assert_postgres_round_trip(database_url, result, strategy, specification))


def test_postgres_store_persists_and_reloads_a_v2_canonical_result() -> None:
    """V2 broker evidence must round-trip with its matching source-run contract."""
    database_url = os.environ.get("THYTRADER_INTEGRATION_DATABASE_URL")
    if database_url is None:
        pytest.skip("THYTRADER_INTEGRATION_DATABASE_URL is not configured")
    strategy = _strategy()
    specification = _v2_run(strategy, "10")
    result = simulate_backtest(specification, strategy, _candles())
    asyncio.run(_assert_postgres_round_trip(database_url, result, strategy, specification))


def test_postgres_store_lists_summaries_without_trade_ledgers() -> None:
    """Discovery rows expose indexed identities plus summary metrics, not the ledger."""
    database_url = os.environ.get("THYTRADER_INTEGRATION_DATABASE_URL")
    if database_url is None:
        pytest.skip("THYTRADER_INTEGRATION_DATABASE_URL is not configured")
    strategy = _strategy()
    specification = _run(strategy)
    result = simulate_backtest(specification, strategy, _candles())
    asyncio.run(_assert_postgres_summary_listing(database_url, result, strategy, specification))


def test_postgres_store_lists_v2_contract_from_canonical_result() -> None:
    """The list projection must expose V2 rather than relabeling it as legacy V1."""
    database_url = os.environ.get("THYTRADER_INTEGRATION_DATABASE_URL")
    if database_url is None:
        pytest.skip("THYTRADER_INTEGRATION_DATABASE_URL is not configured")
    strategy = _strategy()
    specification = _v2_run(strategy, "10")
    result = simulate_backtest(specification, strategy, _candles())
    asyncio.run(_assert_postgres_summary_listing(database_url, result, strategy, specification))


async def _assert_postgres_summary_listing(
    database_url: str,
    result: BacktestResult,
    strategy: StrategyDefinition,
    specification: ResearchRunSpecification,
) -> None:
    """Seed, publish, then read back summary views through the projection query."""
    engine = create_engine(SecretStr(database_url))
    try:
        await _seed_sources(engine, result, strategy, specification)
        store = PostgresBacktestResultStore(engine)
        trace = evaluate_signal_trace(specification, strategy, _candles())
        await store.publish(result, trace=trace)
        fingerprint = backtest_result_fingerprint(result)

        all_rows = await store.list_summaries(limit=10, offset=0)
        by_run = await store.list_summaries(
            run_fingerprint=result.run_fingerprint, limit=10, offset=0
        )
        by_strategy = await store.list_summaries(
            strategy_fingerprint=result.strategy_fingerprint, limit=10, offset=0
        )
        by_dataset = await store.list_summaries(
            dataset_fingerprint=result.dataset_fingerprint, limit=10, offset=0
        )

        assert any(row.result_fingerprint == fingerprint for row in all_rows)
        assert [row.result_fingerprint for row in by_run] == [fingerprint]
        assert [row.result_fingerprint for row in by_strategy] == [fingerprint]
        assert [row.result_fingerprint for row in by_dataset] == [fingerprint]
        row = by_run[0]
        assert row.summary == result.summary
        assert row.engine_contract_version == specification.engine_contract_version
        assert row.published_at.tzinfo is not None
    finally:
        await dispose(engine)


async def _assert_postgres_round_trip(
    database_url: str,
    result: BacktestResult,
    strategy: StrategyDefinition,
    specification: ResearchRunSpecification,
) -> None:
    """Seed immutable source rows, append a result twice, and assert one verified load."""
    engine = create_engine(SecretStr(database_url))
    try:
        await _seed_sources(engine, result, strategy, specification)
        store = PostgresBacktestResultStore(engine)
        trace = evaluate_signal_trace(specification, strategy, _candles())
        published = await store.publish(result, trace=trace)
        republished = await store.publish(result, trace=trace)
        reloaded = await store.load(backtest_result_fingerprint(result))
        assert published == result
        assert republished == result
        assert reloaded == result
    finally:
        await dispose(engine)


async def _seed_sources(
    engine: AsyncEngine,
    result: BacktestResult,
    strategy: StrategyDefinition,
    specification: ResearchRunSpecification,
) -> None:
    """Insert the immutable strategy, binding, and matching source-run publication rows."""
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        await connection.execute(
            delete(published_backtest_results).where(
                published_backtest_results.c.run_fingerprint.in_(
                    select(published_research_run_specs.c.run_fingerprint).where(
                        published_research_run_specs.c.run_id == str(specification.run_id)
                    )
                )
            )
        )
        await connection.execute(
            delete(published_research_run_specs).where(
                published_research_run_specs.c.run_id == str(specification.run_id)
            )
        )
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
                run_id=str(specification.run_id),
                created_at=specification.created_at,
                strategy_fingerprint=result.strategy_fingerprint,
                dataset_fingerprint=result.dataset_fingerprint,
                canonical_specification=canonical_research_run_bytes(specification).decode("utf-8"),
                published_at=now,
            )
            .on_conflict_do_nothing()
        )
