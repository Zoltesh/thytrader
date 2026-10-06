"""Hermetic PostgreSQL coverage for retained paper/live risk evidence and risk0065."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys

from pydantic import SecretStr
import pytest
from sqlalchemy import text

from tests.persistence.test_migration_0048_strategy_root import scratch_database
from tests.risk.test_loss_scope import (
    _STRATEGY_B,
    _TODAY,
    _YESTERDAY,
    _deployment,
    _entry,
    _round_trip,
    _verdict,
)
from thytrader.execution.models import DeploymentMode, DeploymentStatus, IntentPurpose, OrderIntent
from thytrader.execution.service import reset_breaker_latches
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.risk.models import RiskReasonCode
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition

__all__ = ["scratch_database"]
_ROOT = Path(__file__).parents[2]
pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for isolated risk persistence tests.",
)


def _migrate(database_url: str, command: str, target: str) -> subprocess.CompletedProcess[str]:
    """Migrate an owned scratch DB with dotenv disabled and no inherited secret variables."""
    script = (
        "from thytrader.config import Settings; Settings.model_config['env_file'] = None; "
        "from alembic.config import Config; from alembic import command; "
        f"command.{command}(Config('alembic.ini'), '{target}')"
    )
    return subprocess.run(  # noqa: S603 - fixed interpreter/script and test-only URL
        [sys.executable, "-c", script],
        cwd=_ROOT,
        env={"PATH": os.defpath, "THYTRADER_DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


@pytest.mark.parametrize("mode", [DeploymentMode.PAPER, DeploymentMode.LIVE])
def test_stop_delete_restart_reset_and_late_fill_keep_daily_evidence(
    scratch_database: str, mode: DeploymentMode
) -> None:
    """Delete detaches books, not latches/fees/fills/capital; fresh readers retain loss."""
    old = _migrate(scratch_database, "upgrade", "0064")
    assert old.returncode == 0, old.stderr

    async def scenario() -> None:
        """Seed before migration, delete after it, then read through new stores."""
        engine = create_engine(SecretStr(scratch_database))
        try:
            roots = PostgresStrategyStore(engine)
            root = await create_strategy_from_definition(roots, create_template_strategy())
            bound = await roots.snapshot(root.strategy_id)
            loss = _round_trip(
                replace(
                    _deployment(
                        mode=mode,
                        status=DeploymentStatus.STOPPED,
                        created_at=_YESTERDAY,
                        initial_equity=Decimal("0")
                        if mode is DeploymentMode.LIVE
                        else Decimal("10000"),
                        paper_starting_cash=None
                        if mode is DeploymentMode.LIVE
                        else Decimal("10000"),
                        cash=Decimal("0") if mode is DeploymentMode.LIVE else Decimal("10000"),
                        daily_loss_latched=True,
                        performance_capital_quote=Decimal("10000"),
                    ),
                    strategy_id=root.strategy_id,
                    strategy_fingerprint=bound.strategy_fingerprint,
                    strategy_name=root.name,
                    paper_maker_fee_rate=Decimal("0") if mode is DeploymentMode.PAPER else None,
                    paper_taker_fee_rate=Decimal("0") if mode is DeploymentMode.PAPER else None,
                ),
                buy_at=_TODAY.replace(hour=10),
                sell_at=_TODAY.replace(hour=11),
            )
            store = PostgresExecutionStore(engine)
            created = await store.create_deployment(loss.deployment)
            for order in loss.orders:
                await store.save_intent(
                    OrderIntent(
                        id=order.intent_id,
                        deployment_id=created.id,
                        client_order_id=order.client_order_id,
                        product_id=order.product_id,
                        purpose=IntentPurpose.ENTRY,
                        side=order.side,
                        kind=order.kind,
                        quantity=order.quantity,
                        price=order.price,
                        created_at=order.created_at,
                        candle_starts_at=order.created_at,
                    )
                )
                await store.save_order(order)
            # The delayed exit fill is intentionally not in the pre-migration evidence.
            await store.save_fill(loss.fills[0])
            before = await store.get_deployment(created.id)
            upgraded = await asyncio.to_thread(_migrate, scratch_database, "upgrade", "risk0065")
            assert upgraded.returncode == 0, upgraded.stderr
            assert (await store.get_deployment(created.id)) == before
            deleted = await roots.delete(root.strategy_id)
            assert deleted.counts.paper_deployments == 0
            detached = (await PostgresExecutionStore(engine).get_deployment(created.id)).deployment
            assert detached.strategy_id is None
            assert detached.strategy_name == root.name
            assert detached.strategy_fingerprint == bound.strategy_fingerprint
            assert detached.daily_loss_latched
            assert detached.performance_capital_quote == Decimal("10000")
            # A late applied fill remains writable against retained parent evidence.
            await PostgresExecutionStore(engine).save_fill(loss.fills[1])
            reloaded = await PostgresExecutionStore(engine).get_deployment(created.id)
            assert set(reloaded.fills) == set(loss.fills)
            latched = _verdict(
                (reloaded,),
                _entry(strategy_id=_STRATEGY_B),
                mode=mode,
                live_quote_cash=Decimal("10000") if mode is DeploymentMode.LIVE else None,
            )
            assert latched.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT
            assert "latched" in latched.detail
            reset = await reset_breaker_latches(store=store, deployment_id=created.id)
            assert reset.deployment.status is DeploymentStatus.STOPPED
            assert reset.deployment.performance_capital_quote == Decimal("10000")
            reset = await PostgresExecutionStore(engine).get_deployment(created.id)
            retained_loss = _verdict(
                (reset,),
                _entry(strategy_id=_STRATEGY_B),
                mode=mode,
                live_quote_cash=Decimal("10000") if mode is DeploymentMode.LIVE else None,
            )
            assert retained_loss.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT
            assert "latched" not in retained_loss.detail
            if mode is DeploymentMode.PAPER:
                refused = await asyncio.to_thread(_migrate, scratch_database, "downgrade", "0064")
                assert refused.returncode != 0
                assert "detached paper risk evidence exists" in refused.stderr
                assert set((await store.get_deployment(created.id)).fills) == set(loss.fills)
        finally:
            await dispose(engine)

    asyncio.run(scenario())


def test_risk0065_downgrade_without_detached_paper_is_lossless(scratch_database: str) -> None:
    """An empty upgrade/downgrade restores the previous check without touching other tables."""
    upgraded = _migrate(scratch_database, "upgrade", "risk0065")
    assert upgraded.returncode == 0, upgraded.stderr
    downgraded = _migrate(scratch_database, "downgrade", "0064")
    assert downgraded.returncode == 0, downgraded.stderr

    async def check() -> None:
        """Confirm the stored revision is restored on the isolated database."""
        engine = create_engine(SecretStr(scratch_database))
        try:
            async with engine.connect() as connection:
                revision = (
                    await connection.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
                assert revision == "0064"
        finally:
            await dispose(engine)

    asyncio.run(check())
