"""Live PostgreSQL coverage for the 0053 decision journal migration and repository.

Each test creates a throwaway database, runs ``alembic upgrade head`` against it, and
drops it afterwards. Requires ``THYTRADER_TEST_DATABASE_URL`` (an admin-capable URL).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tests.execution.decision_support import RSI_AT_LEAST_50
from tests.execution.test_decision_store import make_decision
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.decision_journal import decision_journal_scope
from thytrader.execution.decision_store import DecisionStoreError
from thytrader.execution.decisions import DecisionOutcome
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    RuntimePhase,
)
from thytrader.execution.paper import PaperBroker
from thytrader.execution.service import create_deployment
from thytrader.execution_worker.service import _run_cycle
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.persistence.audit_events import InMemoryAuditEventStore
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_decisions import PostgresDecisionJournalStore
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.persistence.schema import bar_decisions, deployments
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.strategies.models import StrategyDefinition

if TYPE_CHECKING:
    from collections.abc import Iterator

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
_ROOT = Path(__file__).parents[2]
_NOW = datetime(2026, 3, 1, tzinfo=UTC)
_STRATEGY = UUID("019b76da-a800-776d-8220-17de421ad3e1")
_INVALID_PAYLOAD = '{"not": "a decision"}'

pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL decision-journal coverage.",
)


def _alembic(database_url: str, command: str, target: str) -> subprocess.CompletedProcess[str]:
    """Run one Alembic upgrade/downgrade against an explicit database in a subprocess."""
    environment = {**os.environ, "THYTRADER_DATABASE_URL": database_url}
    return subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", command, target],
        cwd=_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


@pytest.fixture
def migrated_database() -> Iterator[str]:
    """Create one uniquely named database, migrate it to head, and drop it afterwards."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    admin_url = _TEST_DATABASE_URL
    name = f"thytrader_decisions_{uuid4().hex[:12]}"

    async def run(statement: str) -> None:
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(run(f'CREATE DATABASE "{name}"'))
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    try:
        migrated = _alembic(url, "upgrade", "head")
        assert migrated.returncode == 0, migrated.stderr
        yield url
    finally:
        asyncio.run(run(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _deployment(deployment_id: UUID) -> Deployment:
    """A paper book row the decision foreign key can reference."""
    return Deployment(
        id=deployment_id,
        strategy_fingerprint=None,
        strategy_id=None,
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        kind=DeploymentKind.DISCRETIONARY,
        timeframe="1h",
        cash=Decimal("10000"),
        phase=RuntimePhase.FLAT,
        created_at=_NOW,
        updated_at=_NOW,
        paper_starting_cash=Decimal("10000"),
        paper_maker_fee_rate=Decimal("0.001"),
        paper_taker_fee_rate=Decimal("0.002"),
    )


def test_migration_0053_round_trips(migrated_database: str) -> None:
    """Downgrade to 0052 drops only the journal; upgrade recreates it at head 0053."""
    assert _alembic(migrated_database, "downgrade", "0052").returncode == 0

    async def table_exists() -> bool:
        engine = create_async_engine(migrated_database)
        try:
            async with engine.connect() as connection:
                found = await connection.execute(text("SELECT to_regclass('public.bar_decisions')"))
                return found.scalar_one() is not None
        finally:
            await engine.dispose()

    assert asyncio.run(table_exists()) is False
    assert _alembic(migrated_database, "upgrade", "head").returncode == 0
    assert asyncio.run(table_exists()) is True


def test_repository_upserts_pages_filters_and_cascades(migrated_database: str) -> None:
    """Idempotent upserts, keyset pages, filters, strategy view, and FK cascade."""

    async def scenario() -> None:
        engine = create_engine(SecretStr(migrated_database))
        execution = PostgresExecutionStore(engine)
        journal = PostgresDecisionJournalStore(engine)
        paper, live = uuid4(), uuid4()
        try:
            await execution.create_deployment(_deployment(paper))
            await execution.create_deployment(_deployment(live))
            await journal.upsert(make_decision(deployment_id=paper, hour=1))
            await journal.upsert(
                make_decision(
                    deployment_id=paper,
                    hour=1,
                    outcome=DecisionOutcome.ENTRY_SIGNAL,
                    summary="Entry: RSI(14) 55 ≥ 50 → buy 0.1 @ 100 (open)",
                )
            )
            async with engine.connect() as connection:
                count = await connection.execute(select(func.count()).select_from(bar_decisions))
                assert count.scalar_one() == 1
            for hour, outcome in (
                (2, DecisionOutcome.HOLDING),
                (3, DecisionOutcome.EXIT),
                (4, DecisionOutcome.NO_SIGNAL),
            ):
                await journal.upsert(make_decision(deployment_id=paper, hour=hour, outcome=outcome))
            await journal.upsert(make_decision(deployment_id=live, hour=5, product_id="ETH-USD"))
            first = await journal.list_for_deployment(paper, limit=2)
            assert [row.bar_starts_at.hour for row in first.decisions] == [4, 3]
            rest = await journal.list_for_deployment(paper, limit=5, cursor=first.next_cursor)
            assert [row.bar_starts_at.hour for row in rest.decisions] == [2, 1]
            assert rest.decisions[-1].outcome is DecisionOutcome.ENTRY_SIGNAL
            assert rest.next_cursor is None
            trades = await journal.list_for_deployment(
                paper,
                limit=10,
                outcomes=(DecisionOutcome.ENTRY_SIGNAL, DecisionOutcome.EXIT),
            )
            assert [row.outcome for row in trades.decisions] == [
                DecisionOutcome.EXIT,
                DecisionOutcome.ENTRY_SIGNAL,
            ]
            strategy_page = await journal.list_for_strategy(_STRATEGY, limit=10)
            assert strategy_page.decisions[0].deployment_id == live
            narrowed = await journal.list_for_strategy(_STRATEGY, limit=10, deployment_id=live)
            assert [row.product_id for row in narrowed.decisions] == ["ETH-USD"]
            recent = await journal.list_recent(limit=3)
            assert len(recent.decisions) == 3
            previous = await journal.latest_before(paper, "BTC-USD", _NOW + timedelta(hours=4))
            assert previous is not None
            assert previous.bar_starts_at == _NOW + timedelta(hours=3)
            with pytest.raises(DecisionStoreError):
                await journal.list_for_deployment(paper, limit=5, cursor="garbage")
            async with engine.begin() as connection:
                await connection.execute(delete(deployments).where(deployments.c.id == live))
            gone = await journal.list_for_deployment(live, limit=10)
            assert gone.decisions == ()
        finally:
            await dispose(engine)

    asyncio.run(scenario())


def test_a_row_that_no_longer_validates_never_ends_paging_early(migrated_database: str) -> None:
    """A stored row that fails validation is skipped, but the cursor still walks past it."""

    async def scenario() -> None:
        engine = create_engine(SecretStr(migrated_database))
        execution = PostgresExecutionStore(engine)
        journal = PostgresDecisionJournalStore(engine)
        deployment = uuid4()
        try:
            await execution.create_deployment(_deployment(deployment))
            for hour in range(5):
                await journal.upsert(make_decision(deployment_id=deployment, hour=hour))
            async with engine.begin() as connection:
                await connection.execute(
                    update(bar_decisions)
                    .where(bar_decisions.c.bar_starts_at == _NOW + timedelta(hours=3))
                    .values(payload_json=_INVALID_PAYLOAD)
                )
            first = await journal.list_for_deployment(deployment, limit=2)
            assert [row.bar_starts_at.hour for row in first.decisions] == [4]
            assert first.next_cursor is not None
            rest = await journal.list_for_deployment(deployment, limit=5, cursor=first.next_cursor)
            assert [row.bar_starts_at.hour for row in rest.decisions] == [2, 1, 0]
            assert rest.next_cursor is None
        finally:
            await dispose(engine)

    asyncio.run(scenario())


def test_repository_prunes_by_age_and_newest_n_in_bounded_passes(migrated_database: str) -> None:
    """Retention deletes old and excess rows per bot without exceeding the batch limit."""

    async def scenario() -> None:
        engine = create_engine(SecretStr(migrated_database))
        execution = PostgresExecutionStore(engine)
        journal = PostgresDecisionJournalStore(engine)
        busy, quiet = uuid4(), uuid4()
        try:
            await execution.create_deployment(_deployment(busy))
            await execution.create_deployment(_deployment(quiet))
            for hour in range(10):
                await journal.upsert(make_decision(deployment_id=busy, hour=hour))
            await journal.upsert(make_decision(deployment_id=quiet, hour=9))
            now = _NOW + timedelta(hours=10)
            first = await journal.prune(
                now=now, max_rows_per_deployment=4, max_age=timedelta(hours=8), batch_limit=3
            )
            assert first == 3
            second = await journal.prune(
                now=now, max_rows_per_deployment=4, max_age=timedelta(hours=8), batch_limit=100
            )
            assert second == 3
            kept = await journal.list_for_deployment(busy, limit=20)
            assert [row.bar_starts_at.hour for row in kept.decisions] == [9, 8, 7, 6]
            assert len((await journal.list_for_deployment(quiet, limit=20)).decisions) == 1
            assert (
                await journal.prune(
                    now=now,
                    max_rows_per_deployment=4,
                    max_age=timedelta(hours=8),
                    batch_limit=100,
                )
                == 0
            )
        finally:
            await dispose(engine)

    asyncio.run(scenario())


def test_worker_cycle_journals_paper_bar_into_postgres(migrated_database: str) -> None:
    """A real execution cycle writes its bar decision through the PostgreSQL stores."""

    async def scenario() -> None:
        engine = create_engine(SecretStr(migrated_database))
        execution = PostgresExecutionStore(engine)
        strategies = PostgresStrategyStore(engine)
        journal = PostgresDecisionJournalStore(engine)
        try:
            draft = create_template_strategy()
            payload = draft.model_dump(mode="python", by_alias=True)
            payload["entry"]["when"] = dict(RSI_AT_LEAST_50)
            record = await create_strategy_from_definition(
                strategies, StrategyDefinition.model_validate(payload)
            )
            snapshot = await strategies.snapshot(record.strategy_id)
            created = await create_deployment(
                store=execution,
                publication_store=strategies,
                strategy_fingerprint=snapshot.strategy_fingerprint,
                mode=DeploymentMode.PAPER,
                paper_starting_cash=Decimal("10000"),
                live_allowed=False,
            )
            with execution_audit_scope(InMemoryAuditEventStore()), decision_journal_scope(journal):
                await _run_cycle(
                    store=execution,
                    publication_store=strategies,
                    market_data=MarketDataService(DemoMarketData()),
                    paper_broker=PaperBroker(),
                    live_broker=None,
                    quote_reader=None,
                    risk_store=None,
                )
            book = await execution.get_deployment(created.id)
            page = await journal.list_for_deployment(created.id, limit=5)
            assert len(page.decisions) == 1
            decision = page.decisions[0]
            assert decision.bar_starts_at == book.deployment.last_evaluated_bar
            assert decision.strategy_id == record.strategy_id
            assert decision.rule is not None
            by_strategy = await journal.list_for_strategy(record.strategy_id, limit=5)
            assert [row.deployment_id for row in by_strategy.decisions] == [created.id]
        finally:
            await dispose(engine)

    asyncio.run(scenario())
