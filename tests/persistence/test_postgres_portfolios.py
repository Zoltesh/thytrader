"""Live PostgreSQL coverage for portfolios, the strategy-deletion hook, and backtests (ADR 0088).

Needs ``THYTRADER_TEST_DATABASE_URL`` pointing at a database migrated to head. The
migration test creates and drops its own scratch database.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
import os
import subprocess
import sys
from typing import TYPE_CHECKING

from pydantic import SecretStr
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.persistence.test_migration_0048_strategy_root import _ROOT, _alembic, scratch_database
from tests.portfolios.fixtures import datasets_for_two_sleeves
from thytrader.backtest.submission import PostgresBacktestSubmitter
from thytrader.market_data.datasets import DatasetStore
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_portfolios import PostgresPortfolioStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.portfolios.backtest import PortfolioBacktestRequest
from thytrader.portfolios.jobs import PortfolioBacktestRunner
from thytrader.portfolios.models import (
    MutationContext,
    PortfolioCreateRequest,
    PortfolioRevisionConflictError,
    PortfolioSleeveExistsError,
    SetWeightsRequest,
    SleeveAddRequest,
)
from thytrader.portfolios.planning import plan_portfolio_backtest
from thytrader.research.jobs import ResearchJobStatus
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.strategies.library import StrategyRecord

__all__ = ["scratch_database"]

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
_CONTEXT = MutationContext(
    actor="operator", channel="browser", occurred_at=datetime(2026, 10, 2, 12, tzinfo=UTC)
)
_PORTFOLIO_TABLES = (
    "portfolios",
    "portfolio_sleeves",
    "portfolio_journal_entries",
    "portfolio_backtest_jobs",
    "published_portfolio_backtests",
)

pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


def _engine() -> AsyncEngine:
    """Open one engine against the configured integration database."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    return create_engine(SecretStr(_TEST_DATABASE_URL))


async def _strategy(
    store: PostgresStrategyStore, product_id: str, timeframe: str = "1h"
) -> StrategyRecord:
    """Create one valid template strategy."""
    definition = create_template_strategy(product_id=product_id, timeframe=timeframe)
    return await create_strategy_from_definition(store, definition)


def _tables(database_url: str) -> set[str]:
    """Return the portfolio tables present in one database."""

    async def read() -> set[str]:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                rows = await connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
                return {str(row[0]) for row in rows} & set(_PORTFOLIO_TABLES)
        finally:
            await engine.dispose()

    return asyncio.run(read())


def test_migration_0054_adds_and_drops_the_portfolio_tables(scratch_database: str) -> None:
    """Upgrade to head creates every portfolio table; downgrade to 0053 removes them."""
    assert _alembic(scratch_database, "0053").returncode == 0
    assert _tables(scratch_database) == set()
    upgraded = _alembic(scratch_database, "head")
    assert upgraded.returncode == 0, upgraded.stderr
    assert _tables(scratch_database) == set(_PORTFOLIO_TABLES)
    downgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "downgrade", "0053"],
        cwd=_ROOT,
        env={**os.environ, "THYTRADER_DATABASE_URL": scratch_database},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert downgraded.returncode == 0, downgraded.stderr
    assert _tables(scratch_database) == set()


def test_store_round_trips_with_revision_guards_and_journal() -> None:
    """Create, add sleeves, conflict, set weights, journal order, and delete."""

    async def exercise() -> None:
        engine = _engine()
        strategies = PostgresStrategyStore(engine)
        store = PostgresPortfolioStore(engine)
        btc = await _strategy(strategies, "BTC-USDC")
        eth = await _strategy(strategies, "ETH-USDC")
        created = await store.create(
            PortfolioCreateRequest(
                name="Core", mode="live", capital_quote="300", cash_reserve_fraction="0.17"
            ),
            context=_CONTEXT,
        )
        pid = created.portfolio.portfolio_id
        try:
            current = await store.add_sleeve(
                pid,
                SleeveAddRequest(revision=1, strategy_id=btc.strategy_id, weight_fraction="0.5"),
                context=_CONTEXT,
            )
            current = await store.add_sleeve(
                pid,
                SleeveAddRequest(revision=2, strategy_id=eth.strategy_id, weight_fraction="0.33"),
                context=_CONTEXT,
            )
            with pytest.raises(PortfolioRevisionConflictError):
                await store.add_sleeve(
                    pid,
                    SleeveAddRequest(
                        revision=2, strategy_id=eth.strategy_id, weight_fraction="0.1"
                    ),
                    context=_CONTEXT,
                )
            with pytest.raises(PortfolioSleeveExistsError):
                await store.add_sleeve(
                    pid,
                    SleeveAddRequest(
                        revision=3, strategy_id=eth.strategy_id, weight_fraction="0.1"
                    ),
                    context=_CONTEXT,
                )
            assert sorted(view.strategy.name for view in current.sleeves) == sorted(
                [btc.name, eth.name]
            )
            weights = SetWeightsRequest.model_validate(
                {
                    "revision": 3,
                    "weights": [
                        {"sleeve_id": str(view.sleeve.sleeve_id), "weight_fraction": "0.4"}
                        for view in current.sleeves
                    ],
                }
            )
            current = await store.set_weights(pid, weights, context=_CONTEXT)
            assert current.portfolio.revision == 4
            assert [view.sleeve.weight_fraction for view in current.sleeves] == ["0.4", "0.4"]
            journal = await store.journal(pid, limit=10, offset=0)
            assert [entry.kind for entry in journal.entries] == [
                "weights_changed",
                "sleeve_added",
                "sleeve_added",
                "created",
            ]
            assert journal.total == 4
            assert {entry.channel for entry in journal.entries} == {"browser"}
            page = await store.list_page(limit=100, offset=0)
            assert pid in {item.portfolio.portfolio_id for item in page.portfolios}
        finally:
            deletion = await store.delete(
                pid, expected_revision=(await store.get(pid)).portfolio.revision
            )
            assert deletion.sleeves == 2
            await strategies.delete(btc.strategy_id)
            await strategies.delete(eth.strategy_id)
            await dispose(engine)

    asyncio.run(exercise())


def test_deleting_a_strategy_journals_and_removes_its_sleeves() -> None:
    """The strategy-deletion transaction removes the sleeve with a system journal entry."""

    async def exercise() -> None:
        engine = _engine()
        strategies = PostgresStrategyStore(engine)
        store = PostgresPortfolioStore(engine)
        btc = await _strategy(strategies, "BTC-USDC")
        eth = await _strategy(strategies, "ETH-USDC")
        created = await store.create(
            PortfolioCreateRequest(name="Lab", mode="paper", capital_quote="2000"), context=_CONTEXT
        )
        pid = created.portfolio.portfolio_id
        try:
            await store.add_sleeve(
                pid,
                SleeveAddRequest(revision=1, strategy_id=btc.strategy_id, weight_fraction="0.5"),
                context=_CONTEXT,
            )
            await store.add_sleeve(
                pid,
                SleeveAddRequest(revision=2, strategy_id=eth.strategy_id, weight_fraction="0.25"),
                context=_CONTEXT,
            )
            preview = await strategies.preview_deletion(eth.strategy_id)
            assert preview.counts.portfolio_sleeves == 1
            result = await strategies.delete(eth.strategy_id)
            assert result.counts.portfolio_sleeves == 1
            after = await store.get(pid)
            assert [view.strategy.name for view in after.sleeves] == [btc.name]
            assert after.portfolio.revision == 4
            entry = (await store.journal(pid, limit=1, offset=0)).entries[0]
            assert (entry.kind, entry.actor, entry.channel) == (
                "sleeve_removed",
                "system",
                "system",
            )
            assert entry.detail.reason == "strategy_deleted"
            assert entry.detail.strategy_name == eth.name
        finally:
            await store.delete(pid, expected_revision=(await store.get(pid)).portfolio.revision)
            await strategies.delete(btc.strategy_id)
            await dispose(engine)

    asyncio.run(exercise())


def test_portfolio_backtest_runs_real_child_backtests_end_to_end(tmp_path: Path) -> None:
    """Plan, queue, run real unified-model children, combine, store, and reverify."""
    datasets_for_two_sleeves(tmp_path)
    dataset_store = DatasetStore(tmp_path)

    async def exercise() -> None:
        engine = _engine()
        strategies = PostgresStrategyStore(engine)
        store = PostgresPortfolioStore(engine)
        results = PostgresBacktestResultStore(
            engine, research_run_store=PostgresResearchRunStore(engine), dataset_store=dataset_store
        )
        btc = await _strategy(strategies, "BTC-USDC", "1h")
        eth = await _strategy(strategies, "ETH-USDC", "4h")
        created = await store.create(
            PortfolioCreateRequest(
                name="Backtested", mode="paper", capital_quote="1000", cash_reserve_fraction="0.2"
            ),
            context=_CONTEXT,
        )
        pid = created.portfolio.portfolio_id
        try:
            current = await store.add_sleeve(
                pid,
                SleeveAddRequest(revision=1, strategy_id=btc.strategy_id, weight_fraction="0.5"),
                context=_CONTEXT,
            )
            current = await store.add_sleeve(
                pid,
                SleeveAddRequest(revision=2, strategy_id=eth.strategy_id, weight_fraction="0.3"),
                context=_CONTEXT,
            )
            plan = await plan_portfolio_backtest(
                current,
                PortfolioBacktestRequest(
                    maker_fee_rate="0.004", taker_fee_rate="0.006", fixed_slippage_bps="5"
                ),
                strategies=strategies,
                datasets=dataset_store,
            )
            job = await store.create_job(plan, context=_CONTEXT)
            assert await store.claim_next() == job.job_id
            runner = PortfolioBacktestRunner(
                store=store,
                submitter=PostgresBacktestSubmitter(engine, dataset_store),
                results=results,
                datasets=dataset_store,
            )
            await runner.run_job(job.job_id)
            finished = await store.get_job(pid, job.job_id)
            assert finished is not None
            assert finished.status is ResearchJobStatus.COMPLETED, finished.error_message
            fingerprint = str(finished.result_fingerprint)
            result = await store.load_result(pid, fingerprint)
            assert sorted(sleeve.capital_quote for sleeve in result.sleeves) == ["300", "500"]
            for sleeve in result.sleeves:
                child = await results.load(sleeve.result_fingerprint)
                assert child.summary.initial_equity == sleeve.capital_quote
            with localcontext(Context(prec=64)):
                contributions = sum(
                    (Decimal(sleeve.contribution_fraction) for sleeve in result.sleeves),
                    start=Decimal(0),
                )
            assert abs(contributions - Decimal(result.summary.total_return_fraction)) < Decimal(
                "1e-60"
            )
            listing = await store.list_results(pid, limit=5, offset=0)
            assert [row.result_fingerprint for row in listing] == [fingerprint]
            kinds = [entry.kind for entry in (await store.journal(pid, limit=1, offset=0)).entries]
            assert kinds == ["backtest_run"]
        finally:
            await store.delete(pid, expected_revision=(await store.get(pid)).portfolio.revision)
            await strategies.delete(btc.strategy_id)
            await strategies.delete(eth.strategy_id)
            await dispose(engine)

    asyncio.run(exercise())
