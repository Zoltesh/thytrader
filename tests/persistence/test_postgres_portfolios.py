"""Live PostgreSQL coverage for portfolios, the strategy-deletion hook, and backtests (ADR 0088).

Needs ``THYTRADER_TEST_DATABASE_URL`` pointing at a database migrated to head. The
migration test creates and drops its own scratch database.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
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
from thytrader.market_data.datasets import DatasetStore
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_backtest_submitter import PostgresBacktestSubmitter
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
    PortfolioStorageUnavailableError,
    PortfolioStrategyNotFoundError,
    PortfolioValidationError,
    SetWeightsRequest,
    SleeveAddRequest,
    SleevesAddRequest,
)
from thytrader.portfolios.planning import plan_portfolio_backtest
from thytrader.research.jobs import ResearchJobStatus
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from thytrader.portfolios.models import JournalEntry
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
            # Keep queue admission current independently of historical simulation timestamps.
            job_context = replace(_CONTEXT, occurred_at=datetime.now(UTC))
            job = await store.create_job(plan, context=job_context)
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


def test_batch_sleeve_add_is_one_revision_and_all_or_nothing() -> None:
    """``add_sleeves`` writes every sleeve in one transaction and revision (ADR 0094)."""

    async def exercise() -> None:
        engine = _engine()
        strategies = PostgresStrategyStore(engine)
        store = PostgresPortfolioStore(engine)
        records = [await _strategy(strategies, f"{base}-USDC") for base in ("BTC", "ETH", "SOL")]
        created = await store.create(
            PortfolioCreateRequest(name="Batch", mode="paper", capital_quote="1000"),
            context=_CONTEXT,
        )
        pid = created.portfolio.portfolio_id
        try:
            too_heavy = SleevesAddRequest.model_validate(
                {
                    "revision": 1,
                    "sleeves": [
                        {"strategy_id": str(item.strategy_id), "weight_fraction": "0.4"}
                        for item in records
                    ],
                }
            )
            with pytest.raises(PortfolioValidationError):
                await store.add_sleeves(pid, too_heavy, context=_CONTEXT)
            unchanged = await store.get(pid)
            assert (unchanged.portfolio.revision, unchanged.sleeves) == (1, ())
            batch = SleevesAddRequest.model_validate(
                {
                    "revision": 1,
                    "sleeves": [
                        {"strategy_id": str(item.strategy_id), "weight_fraction": "0.3"}
                        for item in records
                    ],
                }
            )
            current = await store.add_sleeves(pid, batch, context=_CONTEXT)
            assert current.portfolio.revision == 2
            assert [view.sleeve.strategy_id for view in current.sleeves] == [
                item.strategy_id for item in records
            ]
            journal = await store.journal(pid, limit=10, offset=0)
            added = [entry for entry in journal.entries if entry.kind == "sleeve_added"]
            assert len(added) == 3
            assert {entry.revision for entry in added} == {2}
            with pytest.raises(PortfolioRevisionConflictError):
                await store.add_sleeves(pid, batch, context=_CONTEXT)
        finally:
            await store.delete(pid, expected_revision=(await store.get(pid)).portfolio.revision)
            for item in records:
                await strategies.delete(item.strategy_id)
            await dispose(engine)

    asyncio.run(exercise())


def test_create_with_sleeves_is_atomic_and_journaled_at_revision_one() -> None:
    """Creation validates all sleeves first and commits the definition at revision 1."""

    async def exercise() -> None:
        """Exercise rejected and successful creation against durable storage."""
        engine = _engine()
        strategies = PostgresStrategyStore(engine)
        store = PostgresPortfolioStore(engine)
        records = [
            await _strategy(strategies, product) for product in ("BTC-USDC", "ETH-USDC", "SOL-USD")
        ]
        initial = (await store.list_page(limit=100, offset=0)).total
        document = {
            "name": "Atomic",
            "mode": "paper",
            "capital_quote": "1000",
            "cash_reserve_fraction": "0.2",
            "sleeves": [
                {
                    "strategy_id": str(records[0].strategy_id),
                    "weight_fraction": "0.5",
                    "note": "Core",
                },
                {"strategy_id": str(records[1].strategy_id), "weight_fraction": "0.3"},
            ],
        }
        try:
            for identity, weight, error_type in (
                (str(records[1].strategy_id), "0.5", PortfolioValidationError),
                (str(records[2].strategy_id), "0.3", PortfolioValidationError),
                ("01a0f000-0000-7000-8000-000000000999", "0.3", PortfolioStrategyNotFoundError),
            ):
                bad = PortfolioCreateRequest.model_validate(
                    {
                        **document,
                        "sleeves": [
                            {"strategy_id": str(records[0].strategy_id), "weight_fraction": "0.5"},
                            {"strategy_id": identity, "weight_fraction": weight},
                        ],
                    }
                )
                with pytest.raises(error_type):
                    await store.create(bad, context=_CONTEXT)
                assert (await store.list_page(limit=100, offset=0)).total == initial
            created = await store.create(
                PortfolioCreateRequest.model_validate(document), context=_CONTEXT
            )
            pid = created.portfolio.portfolio_id
            try:
                assert created.portfolio.revision == 1
                assert [view.sleeve.strategy_id for view in created.sleeves] == [
                    record.strategy_id for record in records[:2]
                ]
                assert [view.sleeve.note for view in created.sleeves] == ["Core", None]
                assert await store.get(pid) == created
                journal = await store.journal(pid, limit=10, offset=0)
                assert [entry.kind for entry in reversed(journal.entries)] == [
                    "created",
                    "sleeve_added",
                    "sleeve_added",
                ]
                assert {entry.revision for entry in journal.entries} == {1}
            finally:
                await store.delete(pid, expected_revision=1)
        finally:
            for record in records:
                await strategies.delete(record.strategy_id)
            await dispose(engine)

    asyncio.run(exercise())


def test_create_rolls_back_portfolio_and_sleeves_when_journal_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PostgreSQL error after portfolio and sleeve inserts leaves no partial rows."""

    async def fail_journal(connection: AsyncConnection, entries: Sequence[JournalEntry]) -> None:
        """Raise an actual database error at the final write stage of creation."""
        assert len(entries) == 3
        await connection.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(
        "thytrader.persistence.postgres_portfolio_rows.insert_journal", fail_journal
    )

    async def exercise() -> None:
        """Compare row counts before and after the aborted transaction."""
        engine = _engine()
        strategies = PostgresStrategyStore(engine)
        store = PostgresPortfolioStore(engine)
        records = [await _strategy(strategies, product) for product in ("BTC-USDC", "ETH-USDC")]

        async def counts() -> tuple[int, ...]:
            """Count only the operational tables this create transaction writes."""
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            "SELECT (SELECT count(*) FROM portfolios), "
                            "(SELECT count(*) FROM portfolio_sleeves), "
                            "(SELECT count(*) FROM portfolio_journal_entries)"
                        )
                    )
                ).one()
                return (int(row[0]), int(row[1]), int(row[2]))

        try:
            before = await counts()
            request = PortfolioCreateRequest.model_validate(
                {
                    "name": "Rollback",
                    "mode": "paper",
                    "capital_quote": "1000",
                    "sleeves": [
                        {"strategy_id": str(record.strategy_id), "weight_fraction": "0.4"}
                        for record in records
                    ],
                }
            )
            with pytest.raises(PortfolioStorageUnavailableError):
                await store.create(request, context=_CONTEXT)
            assert await counts() == before
        finally:
            for record in records:
                await strategies.delete(record.strategy_id)
            await dispose(engine)

    asyncio.run(exercise())
