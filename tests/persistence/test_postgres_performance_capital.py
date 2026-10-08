"""Performance budgets and loss evidence survive migration, rebalance, and restart."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
import os
import subprocess
import sys

from pydantic import SecretStr
import pytest
from sqlalchemy import text

from tests.execution.test_performance_capital import _NOW, _live_snapshot
from tests.persistence.test_migration_0048_strategy_root import _ROOT, _alembic, scratch_database
from thytrader.execution.capital import refresh_performance
from thytrader.execution.service import reset_breaker_latches
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.gate import evaluate_runtime_breakers
from thytrader.risk.models import RiskReasonCode, compiled_default_risk_policy
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.trading.models import DeploymentSnapshot, DeploymentStatus, RuntimePhase

__all__ = ["scratch_database"]
pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for performance-capital persistence coverage.",
)


@pytest.mark.anyio
async def test_loss_budget_and_latch_survive_restart_and_allocation_change(
    scratch_database: str,
) -> None:
    """Fresh stores recover the funded peak, worst loss, pinned denominator, and latch."""
    migrated = await asyncio.to_thread(_alembic, scratch_database, "head")
    assert migrated.returncode == 0, migrated.stderr
    engine = create_engine(SecretStr(scratch_database))
    try:
        strategies = PostgresStrategyStore(engine)
        root = await create_strategy_from_definition(strategies, create_template_strategy())
        bound = await strategies.snapshot(root.strategy_id)
        store = PostgresExecutionStore(engine)
        deployment = replace(
            _live_snapshot().deployment,
            strategy_id=root.strategy_id,
            strategy_fingerprint=bound.strategy_fingerprint,
            phase=RuntimePhase.FLAT,
            cash=Decimal("10"),
        )
        created = await store.create_deployment(deployment)
        peak = refresh_performance(DeploymentSnapshot(created), now=_NOW)
        saved = await store.save_deployment(peak, expected_revision=created.revision)
        loss = refresh_performance(
            DeploymentSnapshot(replace(saved, cash=Decimal("-5.5"))), now=_NOW
        )
        await store.save_deployment(
            replace(loss, status=DeploymentStatus.PAUSED, drawdown_latched=True),
            expected_revision=saved.revision,
        )
    finally:
        await dispose(engine)
    engine = create_engine(SecretStr(scratch_database))
    try:
        restarted = PostgresExecutionStore(engine)
        recovered = await restarted.get_deployment(created.id)
        assert recovered.deployment.cash == Decimal("-5.5")
        assert recovered.deployment.initial_equity == Decimal("0")
        assert recovered.deployment.performance_capital_quote == Decimal("100")
        assert recovered.deployment.high_water_mark_equity == Decimal("10")
        assert recovered.deployment.performance_maximum_drawdown_fraction == Decimal(
            "15.5"
        ) / Decimal("110")
        assert recovered.deployment.drawdown_latched
        rebalanced = await restarted.save_deployment(
            replace(recovered.deployment, allocated_capital=Decimal("200")),
            expected_revision=recovered.deployment.revision,
        )
        verdict = evaluate_runtime_breakers(
            compiled_default_risk_policy().model_copy(
                update={"max_strategy_drawdown_fraction": "0.1"}
            ),
            mode=rebalanced.mode,
            snapshot=DeploymentSnapshot(rebalanced),
            snapshots=(DeploymentSnapshot(rebalanced),),
            live_quote_cash=Decimal("1000"),
            observation=EntryObservation(
                as_of=_NOW,
                proposed_price=None,
                reference_price=Decimal("50"),
                marks={"BTC-USD": Decimal("50")},
            ),
        )
        assert verdict.reason_code is RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT
        reset = await reset_breaker_latches(store=restarted, deployment_id=created.id)
        assert not reset.deployment.drawdown_latched
        assert reset.deployment.status is DeploymentStatus.PAUSED
        assert reset.deployment.performance_capital_quote == Decimal("100")
        assert (
            reset.deployment.performance_maximum_drawdown_fraction
            == recovered.deployment.performance_maximum_drawdown_fraction
        )
        after_reset = evaluate_runtime_breakers(
            compiled_default_risk_policy().model_copy(
                update={"max_strategy_drawdown_fraction": "0.1"}
            ),
            mode=reset.deployment.mode,
            snapshot=reset,
            snapshots=(reset,),
            live_quote_cash=Decimal("1000"),
            observation=EntryObservation(
                as_of=_NOW,
                proposed_price=None,
                reference_price=Decimal("50"),
                marks={"BTC-USD": Decimal("50")},
            ),
        )
        assert after_reset.reason_code is RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT
    finally:
        await dispose(engine)


def _downgrade(database_url: str) -> subprocess.CompletedProcess[str]:
    """Reverse only the performance migration on an owned scratch database."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0060"],
        cwd=_ROOT,
        env={**os.environ, "THYTRADER_DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def test_migration_preserves_legacy_balances_and_refuses_lossy_downgrade(
    scratch_database: str,
) -> None:
    """An old zero ledger is unchanged; only new metadata prevents downgrade."""
    old = _alembic(scratch_database, "0060")
    assert old.returncode == 0, old.stderr
    deployment = _live_snapshot().deployment

    async def seed() -> None:
        """Write a pre-upgrade discretionary row without requiring new application columns."""
        engine = create_engine(SecretStr(scratch_database))
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO deployments (id, product_id, mode, status, kind, timeframe, "
                        "cash, phase, allocated_capital, initial_equity, baseline_equity, "
                        "high_water_mark_equity, created_at, updated_at) VALUES "
                        "(:id, 'BTC-USD', 'live', 'stopped', 'discretionary', '1h', '-2', "
                        "'flat', '20', '0', '0', '0', :now, :now)"
                    ),
                    {"id": deployment.id, "now": _NOW},
                )
        finally:
            await dispose(engine)

    async def check(*, pin: bool = False) -> None:
        """Check unchanged legacy evidence and optionally pin the new metadata."""
        engine = create_engine(SecretStr(scratch_database))
        try:
            async with engine.begin() as connection:
                row = (
                    (
                        await connection.execute(
                            text(
                                "SELECT cash, initial_equity, baseline_equity, "
                                "high_water_mark_equity, "
                                "performance_capital_quote, performance_maximum_drawdown_fraction "
                                "FROM deployments WHERE id=:id"
                            ),
                            {"id": deployment.id},
                        )
                    )
                    .mappings()
                    .one()
                )
                assert (
                    row["cash"],
                    row["initial_equity"],
                    row["baseline_equity"],
                    row["high_water_mark_equity"],
                ) == ("-2", "0", "0", "0")
                assert row["performance_capital_quote"] is None
                assert row["performance_maximum_drawdown_fraction"] is None
                if pin:
                    await connection.execute(
                        text(
                            "UPDATE deployments SET performance_capital_quote='20', "
                            "performance_maximum_drawdown_fraction='0.1' WHERE id=:id"
                        ),
                        {"id": deployment.id},
                    )
        finally:
            await dispose(engine)

    asyncio.run(seed())
    upgraded = _alembic(scratch_database, "head")
    assert upgraded.returncode == 0, upgraded.stderr
    asyncio.run(check())
    reversed_ = _downgrade(scratch_database)
    assert reversed_.returncode == 0, reversed_.stderr
    assert _alembic(scratch_database, "head").returncode == 0
    asyncio.run(check(pin=True))
    refused = _downgrade(scratch_database)
    assert refused.returncode != 0
    assert "Cannot downgrade 0061" in refused.stderr
