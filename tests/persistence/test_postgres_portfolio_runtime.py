"""Live PostgreSQL coverage for portfolio deployment, breakers, and proposals (ADR 0091).

Needs ``THYTRADER_TEST_DATABASE_URL`` pointing at a disposable test server. Each
scenario creates, migrates, and drops its own database; retained account evidence
from other suites must not change this scenario's policy/funding assumptions.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
import subprocess
import sys
from typing import TYPE_CHECKING

from pydantic import SecretStr
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.persistence.test_migration_0048_strategy_root import _ROOT, _alembic, scratch_database
from tests.persistence.test_risk_retention import _migrate
from thytrader.execution.models import DeploymentStatus
from thytrader.execution_worker.portfolio_supervisor import supervise_portfolios
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_portfolios import PostgresPortfolioStore
from thytrader.persistence.postgres_risk import PostgresRiskPolicyStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.portfolios.manager import ProposalService
from thytrader.portfolios.models import (
    ManagerPermissions,
    ManagerSettings,
    MutationContext,
    PortfolioConflictError,
    PortfolioCreateRequest,
    PortfolioLimits,
    SleeveAddRequest,
)
from thytrader.portfolios.proposals import ProposalDecisionRequest, ProposalSubmitRequest
from thytrader.portfolios.runtime import PortfolioRuntimeService
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition

if TYPE_CHECKING:
    from thytrader.execution.models import Deployment
    from thytrader.portfolios.models import PortfolioAggregate

__all__ = ["scratch_database"]

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


def _operator() -> MutationContext:
    """An operator acting now through the API."""
    return MutationContext(actor="operator", channel="api", occurred_at=datetime.now(UTC))


def _objects(database_url: str) -> tuple[set[str], bool]:
    """The runtime tables present and whether deployments has portfolio_id."""

    async def read() -> tuple[set[str], bool]:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                tables = await connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
                columns = await connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'deployments' AND column_name = 'portfolio_id'"
                    )
                )
                names = {str(row[0]) for row in tables} & {
                    "portfolio_runtime",
                    "portfolio_proposals",
                }
                return names, columns.first() is not None
        finally:
            await engine.dispose()

    return asyncio.run(read())


def test_migration_0056_adds_and_drops_the_runtime_objects(scratch_database: str) -> None:
    """Upgrade adds runtime state, proposals, and the deployment tag; downgrade removes them."""
    assert _alembic(scratch_database, "0055").returncode == 0
    assert _objects(scratch_database) == (set(), False)
    upgraded = _alembic(scratch_database, "head")
    assert upgraded.returncode == 0, upgraded.stderr
    assert _objects(scratch_database) == ({"portfolio_runtime", "portfolio_proposals"}, True)
    downgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "downgrade", "0055"],
        cwd=_ROOT,
        env={**os.environ, "THYTRADER_DATABASE_URL": scratch_database},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert downgraded.returncode == 0, downgraded.stderr
    assert _objects(scratch_database) == (set(), False)


async def _portfolio(
    strategies: PostgresStrategyStore, store: PostgresPortfolioStore
) -> PortfolioAggregate:
    """A paper portfolio with BTC (50%) and ETH (30%) sleeves on 1,000 capital."""
    current = await store.create(
        PortfolioCreateRequest(
            name="Runtime",
            mode="paper",
            capital_quote="1000",
            cash_reserve_fraction="0.2",
            limits=PortfolioLimits(max_drawdown_fraction="0.2"),
            manager=ManagerSettings(
                permissions=ManagerPermissions(may_rebalance=True, max_weight_change_per_week="0.1")
            ),
        ),
        context=_operator(),
    )
    for product_id, weight in (("BTC-USDC", "0.5"), ("ETH-USDC", "0.3")):
        record = await create_strategy_from_definition(
            strategies, create_template_strategy(product_id=product_id, timeframe="1h")
        )
        current = await store.add_sleeve(
            current.portfolio.portfolio_id,
            SleeveAddRequest(
                revision=current.portfolio.revision,
                strategy_id=record.strategy_id,
                weight_fraction=weight,
            ),
            context=_operator(),
        )
    return current


def _rebalance(
    current: PortfolioAggregate, weights: tuple[str, str], deployment_id: str
) -> ProposalSubmitRequest:
    """A rebalance of both sleeves citing one sleeve bot."""
    return ProposalSubmitRequest.model_validate(
        {
            "revision": current.portfolio.revision,
            "change": {
                "kind": "rebalance",
                "weights": [
                    {"sleeve_id": str(view.sleeve.sleeve_id), "weight_fraction": weight}
                    for view, weight in zip(current.sleeves, weights, strict=True)
                ],
            },
            "rationale": "Shift weight by the evidence.",
            "evidence": [{"kind": "deployment", "ref": deployment_id}],
        }
    )


def test_deploy_supervise_propose_and_delete_against_postgres(scratch_database: str) -> None:
    """A fresh policy domain exercises tagged books, CAS, breakers, proposals, and deletion."""
    upgraded = _migrate(scratch_database, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    async def exercise() -> None:
        """Keep all end-to-end assertions without clearing or ignoring foreign account evidence."""
        engine = create_engine(SecretStr(scratch_database))
        strategies = PostgresStrategyStore(engine)
        execution = PostgresExecutionStore(engine)
        store = PostgresPortfolioStore(engine)
        runtime = PortfolioRuntimeService(
            portfolios=store,
            execution=execution,
            strategies=strategies,
            publication=strategies,
            risk_store=PostgresRiskPolicyStore(engine),
            live_allowed=False,
        )
        proposals = ProposalService(portfolios=store, execution=execution, runtime=runtime)
        current = await _portfolio(strategies, store)
        pid = current.portfolio.portfolio_id
        books: list[Deployment] = []
        try:
            started = await runtime.start(
                pid, revision=current.portfolio.revision, context=_operator()
            )
            assert [item.outcome for item in started.outcomes] == ["started", "started"]
            books = [
                item for item in await execution.list_deployments() if item.portfolio_id == pid
            ]
            assert sorted(book.paper_starting_cash or Decimal(0) for book in books) == [
                Decimal("300"),
                Decimal("500"),
            ]
            first = books[0]
            await execution.save_deployment(replace(first, portfolio_id=None))
            kept = (await execution.get_deployment(first.id)).deployment
            assert kept.portfolio_id == pid
            state = await store.runtime_state(pid)
            assert state.revision == 1
            assert await store.write_runtime(state, expected_revision=0) is None

            starting = kept.paper_starting_cash or Decimal(0)
            await execution.save_deployment(
                replace(
                    kept,
                    initial_equity=starting,
                    performance_equity=starting - Decimal("250"),
                    high_water_mark_equity=starting,
                )
            )
            gate = await supervise_portfolios(
                store=execution,
                portfolios=store,
                deployments=await execution.list_deployments(),
                now=datetime.now(UTC),
            )
            assert gate[pid].breaker_reason is not None
            latched = await store.runtime_state(pid)
            assert latched.breaker_reason == "PORTFOLIO_DRAWDOWN_STOP"
            paused = [
                item for item in await execution.list_deployments() if item.portfolio_id == pid
            ]
            assert {item.status for item in paused} == {DeploymentStatus.PAUSED}
            cleared = await runtime.reset_breaker(pid, context=_operator())
            assert cleared.breaker_reason is None

            fresh = await store.get(pid)
            applied = await proposals.submit(
                pid, _rebalance(fresh, ("0.45", "0.35"), str(first.id)), channel="api"
            )
            assert (applied.proposal.status, applied.proposal.weight_moved) == ("applied", "0.05")
            heavy = _rebalance(applied.aggregate, ("0.35", "0.45"), str(first.id))
            pending = await proposals.submit(pid, heavy, channel="api")
            assert pending.proposal.status == "pending"
            declined = await proposals.decline(
                pid, pending.proposal.proposal_id, ProposalDecisionRequest(), context=_operator()
            )
            assert declined.proposal.status == "declined"
            stale = await proposals.submit(
                pid,
                heavy.model_copy(update={"revision": declined.aggregate.portfolio.revision}),
                channel="api",
                now=datetime.now(UTC) - timedelta(days=8),
            )
            listing = await proposals.list_proposals(pid, status=None, limit=10, offset=0)
            statuses = {item.proposal_id: item.status for item in listing.proposals}
            assert statuses[stale.proposal.proposal_id] == "expired"
            assert listing.proposals[-1].proposal_id == stale.proposal.proposal_id
            kinds = [entry.kind for entry in (await store.journal(pid, limit=50, offset=0)).entries]
            for kind in (
                "deployment_started",
                "breaker_tripped",
                "breaker_reset",
                "proposal_submitted",
                "proposal_declined",
                "weights_changed",
            ):
                assert kind in kinds

            with pytest.raises(PortfolioConflictError):
                await store.delete(pid, expected_revision=(await store.get(pid)).portfolio.revision)
        finally:
            await runtime.stop(pid, context=_operator())
            await store.delete(pid, expected_revision=(await store.get(pid)).portfolio.revision)
            orphans = [
                item
                for item in await execution.list_deployments()
                if item.id in {b.id for b in books}
            ]
            assert {item.portfolio_id for item in orphans} == {None}
            await dispose(engine)

    asyncio.run(exercise())
