"""Real PostgreSQL twin uniqueness, worker-save isolation, restart, and migration gates."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import os
import subprocess
import sys

from pydantic import SecretStr
import pytest
from sqlalchemy import text

from tests.execution.test_fill_comparison import _deployment
from tests.persistence.test_migration_0048_strategy_root import _ROOT, _alembic, scratch_database
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.strategies.authoring import create_template_strategy, new_strategy_identity
from thytrader.strategies.library import clone_strategy, create_strategy_from_definition
from thytrader.trading.models import DeploymentMode, DeploymentStatus
from thytrader.trading.twins import DeploymentTwinLink, TwinConflictError, TwinValidationError

__all__ = ["scratch_database"]
pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL twin coverage.",
)


@pytest.mark.anyio
async def test_competing_links_survive_restart_and_worker_saves(scratch_database: str) -> None:
    """One contender wins each member; reversal is idempotent and stale removal conflicts."""
    migrated = _alembic(scratch_database, "head")
    assert migrated.returncode == 0, migrated.stderr
    engine = create_engine(SecretStr(scratch_database))
    try:
        strategies = PostgresStrategyStore(engine)
        definition = create_template_strategy(now=datetime.now(UTC))
        record = await create_strategy_from_definition(strategies, definition)
        snapshot = await strategies.snapshot(record.strategy_id)
        store = PostgresExecutionStore(engine)
        paper, live, other = tuple(
            replace(
                _deployment(mode),
                strategy_id=record.strategy_id,
                strategy_fingerprint=snapshot.strategy_fingerprint,
                status=DeploymentStatus.STOPPED,
                paper_maker_fee_rate=None if mode is DeploymentMode.LIVE else Decimal("0.001"),
                paper_taker_fee_rate=None if mode is DeploymentMode.LIVE else Decimal("0.002"),
            )
            for mode in (DeploymentMode.PAPER, DeploymentMode.LIVE, DeploymentMode.LIVE)
        )
        for bot in (paper, live, other):
            await store.create_deployment(bot)
        outcomes = await asyncio.gather(
            store.link_twins(paper.id, live.id),
            store.link_twins(paper.id, other.id),
            return_exceptions=True,
        )
        assert sum(isinstance(item, TwinConflictError) for item in outcomes) == 1
        link = next(item for item in outcomes if isinstance(item, DeploymentTwinLink))
        assert await store.link_twins(link.live_deployment_id, paper.id) == link
        await store.save_deployment(replace(paper, mismatch_detail="worker save"))
        assert await store.get_twin_link(paper.id) == link
    finally:
        await dispose(engine)
    engine = create_engine(SecretStr(scratch_database))
    try:
        restarted = PostgresExecutionStore(engine)
        assert await restarted.get_twin_link(link.live_deployment_id) == link
        assert await restarted.list_twin_links() == (link,)
        refused = await asyncio.to_thread(_downgrade, scratch_database)
        assert refused.returncode != 0
        assert "Cannot downgrade 0060" in refused.stderr
        assert await restarted.get_twin_link(paper.id) == link

        await restarted.unlink_twins(paper.id, link.live_deployment_id)
        await restarted.unlink_twins(paper.id, link.live_deployment_id)
        replacement = other if link.live_deployment_id == live.id else live
        await restarted.link_twins(paper.id, replacement.id)
        with pytest.raises(TwinConflictError):
            await restarted.unlink_twins(paper.id, link.live_deployment_id)
        current = await restarted.get_twin_link(paper.id)
        assert current is not None and current.live_deployment_id == replacement.id
    finally:
        await dispose(engine)


def test_migration_starts_unlinked_and_empty_downgrade_is_safe(scratch_database: str) -> None:
    """0060 never invents partners, and an empty migration can be reversed and reapplied."""
    migrated = _alembic(scratch_database, "head")
    assert migrated.returncode == 0, migrated.stderr

    async def count() -> int:
        """Count the new relationship without touching any trading records."""
        engine = create_engine(SecretStr(scratch_database))
        try:
            async with engine.connect() as conn:
                return (
                    await conn.execute(text("SELECT COUNT(*) FROM deployment_twin_links"))
                ).scalar_one()
        finally:
            await dispose(engine)

    assert asyncio.run(count()) == 0
    downgraded = _downgrade(scratch_database)
    assert downgraded.returncode == 0, downgraded.stderr
    assert _alembic(scratch_database, "head").returncode == 0
    assert asyncio.run(count()) == 0


def _downgrade(database_url: str) -> subprocess.CompletedProcess[str]:
    """Run the reverse migration with typed text output against a scratch database."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0059"],
        cwd=_ROOT,
        env={**os.environ, "THYTRADER_DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


@pytest.mark.anyio
async def test_cloned_twins_require_bound_rule_proof_and_survive_restart(
    scratch_database: str,
) -> None:
    """Different root identities link only with verified pinned rules inside the locked write."""
    migrated = _alembic(scratch_database, "head")
    assert migrated.returncode == 0, migrated.stderr
    engine = create_engine(SecretStr(scratch_database))
    try:
        strategies = PostgresStrategyStore(engine)
        live_root = await create_strategy_from_definition(
            strategies, create_template_strategy(product_id="ETH-USD")
        )
        clone_id, clone_created_at = new_strategy_identity()
        paper_root = await clone_strategy(
            strategies,
            live_root.strategy_id,
            name="Research paper clone",
            strategy_id=clone_id,
            created_at=clone_created_at,
        )
        live_snapshot = await strategies.snapshot(live_root.strategy_id)
        paper_snapshot = await strategies.snapshot(paper_root.strategy_id)
        store = PostgresExecutionStore(engine)
        live = replace(
            _deployment(DeploymentMode.LIVE),
            strategy_id=live_root.strategy_id,
            strategy_fingerprint=live_snapshot.strategy_fingerprint,
        )
        paper = replace(
            _deployment(DeploymentMode.PAPER),
            strategy_id=paper_root.strategy_id,
            strategy_fingerprint=paper_snapshot.strategy_fingerprint,
            paper_maker_fee_rate=Decimal("0.001"),
            paper_taker_fee_rate=Decimal("0.002"),
        )
        for bot in (paper, live):
            await store.create_deployment(bot)
        with pytest.raises(TwinValidationError):
            await store.link_twins(paper.id, live.id)
        with pytest.raises(TwinValidationError):
            await store.link_twins(paper.id, live.id, snapshots=(live_snapshot, paper_snapshot))
        link = await store.link_twins(paper.id, live.id, snapshots=(paper_snapshot, live_snapshot))
        assert link.paper_deployment_id == paper.id
        assert link.live_deployment_id == live.id
        assert (await store.get_deployment(paper.id)).deployment.status is paper.status
    finally:
        await dispose(engine)
    engine = create_engine(SecretStr(scratch_database))
    try:
        assert await PostgresExecutionStore(engine).get_twin_link(live.id) == link
    finally:
        await dispose(engine)
