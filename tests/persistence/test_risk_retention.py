"""Hermetic PostgreSQL coverage for retained paper/live risk evidence and risk0065."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING

from pydantic import SecretStr
import pytest
from sqlalchemy import MetaData, Table, Text, func, select, text

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
from thytrader.execution.models import DeploymentMode, DeploymentStatus, IntentPurpose
from thytrader.execution.service import reset_breaker_latches
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.risk.models import RiskReasonCode
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import canonical_strategy_bytes, strategy_fingerprint

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import AsyncConnection

    from thytrader.execution.models import DeploymentSnapshot
    from thytrader.strategies.models import StrategyDefinition

__all__ = ["scratch_database"]
_ROOT = Path(__file__).parents[2]
_LEGACY_TABLE_NAMES = (
    "strategies",
    "strategy_snapshots",
    "deployments",
    "order_intents",
    "execution_orders",
    "execution_fills",
    "execution_positions",
    "execution_instrument_state",
)
pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for isolated risk persistence tests.",
)


@dataclass(frozen=True, slots=True)
class _LegacyTableEvidence:
    """Lossless SQL row images plus their actual legacy column projection.

    PostgreSQL renders the reflected rows as JSON text at this schema boundary;
    no loose row dictionaries or current-model defaults stand in for old evidence.
    New HEAD columns are deliberately outside the captured legacy projection.
    """

    table: Table
    rows: tuple[str, ...]


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


def _reflect_legacy(connection: Connection) -> MetaData:
    """Read only real 0064 tables, without using current persistence metadata."""
    metadata = MetaData()
    metadata.reflect(bind=connection, only=_LEGACY_TABLE_NAMES)
    assert "venue_observed_at" not in metadata.tables["execution_orders"].c
    return metadata


async def _legacy_rows(connection: AsyncConnection, table: Table) -> tuple[str, ...]:
    """Capture all old columns losslessly, even after HEAD adds new columns."""
    projection = select(*table.c).subquery()
    statement = select(func.row_to_json(projection.table_valued()).cast(Text))
    return tuple(sorted((await connection.execute(statement)).scalars()))


async def _capture_legacy(
    connection: AsyncConnection, metadata: MetaData
) -> tuple[_LegacyTableEvidence, ...]:
    """Snapshot financial rows, identity, canonical snapshot bytes, and empty inventory."""
    evidence: list[_LegacyTableEvidence] = []
    for name in _LEGACY_TABLE_NAMES:
        table = metadata.tables[name]
        evidence.append(_LegacyTableEvidence(table, await _legacy_rows(connection, table)))
    return tuple(evidence)


async def _assert_legacy_unchanged(
    connection: AsyncConnection, evidence: tuple[_LegacyTableEvidence, ...]
) -> None:
    """Compare actual old-column row images rather than a latest-schema store snapshot."""
    for item in evidence:
        assert await _legacy_rows(connection, item.table) == item.rows, item.table.name


def _legacy_loss(mode: DeploymentMode) -> tuple[StrategyDefinition, DeploymentSnapshot]:
    """Build explicit stopped-book evidence with a delayed second fill, as before the upgrade."""
    definition = create_template_strategy(product_id="BTC-USD")
    loss = _round_trip(
        replace(
            _deployment(
                mode=mode,
                status=DeploymentStatus.STOPPED,
                created_at=_YESTERDAY,
                initial_equity=Decimal("0") if mode is DeploymentMode.LIVE else Decimal("10000"),
                paper_starting_cash=None if mode is DeploymentMode.LIVE else Decimal("10000"),
                cash=Decimal("0") if mode is DeploymentMode.LIVE else Decimal("10000"),
                daily_loss_latched=True,
                performance_capital_quote=Decimal("10000"),
            ),
            strategy_id=definition.strategy_id,
            strategy_fingerprint=strategy_fingerprint(definition),
            strategy_name=definition.name,
            paper_maker_fee_rate=Decimal("0") if mode is DeploymentMode.PAPER else None,
            paper_taker_fee_rate=Decimal("0") if mode is DeploymentMode.PAPER else None,
        ),
        buy_at=_TODAY.replace(hour=10),
        sell_at=_TODAY.replace(hour=11),
    )
    return definition, loss


async def _seed_legacy(
    connection: AsyncConnection,
    metadata: MetaData,
    definition: StrategyDefinition,
    loss: DeploymentSnapshot,
) -> None:
    """Insert known 0064 columns directly, never invoking a current guarded store."""
    canonical = canonical_strategy_bytes(definition).decode("utf-8")
    deployment = loss.deployment
    assert deployment.initial_equity is not None
    assert deployment.performance_capital_quote is not None
    await connection.execute(
        metadata.tables["strategies"]
        .insert()
        .values(
            strategy_id=str(definition.strategy_id),
            name=definition.name,
            product_id=definition.instrument.product_id,
            timeframe=definition.timeframe,
            document=canonical,
            is_valid=True,
            current_fingerprint=deployment.strategy_fingerprint,
            revision=1,
            created_at=_YESTERDAY,
            updated_at=_YESTERDAY,
        )
    )
    await connection.execute(
        metadata.tables["strategy_snapshots"]
        .insert()
        .values(
            strategy_fingerprint=deployment.strategy_fingerprint,
            strategy_id=str(definition.strategy_id),
            canonical_definition=canonical,
            created_at=_YESTERDAY,
        )
    )
    await connection.execute(
        metadata.tables["deployments"]
        .insert()
        .values(
            id=deployment.id,
            strategy_fingerprint=deployment.strategy_fingerprint,
            strategy_id=str(definition.strategy_id),
            strategy_name=deployment.strategy_name,
            product_id=deployment.product_id,
            mode=deployment.mode.value,
            status=deployment.status.value,
            kind=deployment.kind.value,
            paper_starting_cash="10000" if deployment.mode is DeploymentMode.PAPER else None,
            paper_maker_fee_rate="0" if deployment.mode is DeploymentMode.PAPER else None,
            paper_taker_fee_rate="0" if deployment.mode is DeploymentMode.PAPER else None,
            cash=str(deployment.cash),
            phase=deployment.phase.value,
            initial_equity=str(deployment.initial_equity),
            daily_loss_latched=deployment.daily_loss_latched,
            performance_capital_quote=str(deployment.performance_capital_quote),
            created_at=deployment.created_at,
            updated_at=deployment.updated_at,
        )
    )
    for order in loss.orders:
        await connection.execute(
            metadata.tables["order_intents"]
            .insert()
            .values(
                id=order.intent_id,
                deployment_id=deployment.id,
                client_order_id=order.client_order_id,
                product_id=order.product_id,
                purpose=IntentPurpose.ENTRY.value,
                side=order.side.value,
                kind=order.kind.value,
                quantity=str(order.quantity),
                price=None if order.price is None else str(order.price),
                status="pending",
                created_at=order.created_at,
                candle_starts_at=order.created_at,
            )
        )
        await connection.execute(
            metadata.tables["execution_orders"]
            .insert()
            .values(
                id=order.id,
                deployment_id=deployment.id,
                intent_id=order.intent_id,
                client_order_id=order.client_order_id,
                product_id=order.product_id,
                side=order.side.value,
                kind=order.kind.value,
                quantity=str(order.quantity),
                price=None if order.price is None else str(order.price),
                filled_quantity=str(order.filled_quantity),
                status=order.status.value,
                created_at=order.created_at,
                updated_at=order.updated_at,
            )
        )
    # The delayed exit fill is intentionally not in the pre-migration evidence.
    fill = loss.fills[0]
    await connection.execute(
        metadata.tables["execution_fills"]
        .insert()
        .values(
            id=fill.id,
            deployment_id=deployment.id,
            order_id=fill.order_id,
            venue_fill_id=fill.venue_fill_id,
            price=str(fill.price),
            quantity=str(fill.quantity),
            fee=str(fill.fee),
            filled_at=fill.filled_at,
            economics_applied_at=fill.economics_applied_at,
        )
    )


@pytest.mark.parametrize("mode", [DeploymentMode.PAPER, DeploymentMode.LIVE])
def test_stop_delete_restart_reset_and_late_fill_keep_daily_evidence(
    scratch_database: str, mode: DeploymentMode
) -> None:
    """Seed old SQL, migrate to HEAD, then retain loss through current store operations."""
    old = _migrate(scratch_database, "upgrade", "0064")
    assert old.returncode == 0, old.stderr

    async def scenario() -> None:
        """Current stores are used only after all their schema and guard migrations exist."""
        engine = create_engine(SecretStr(scratch_database))
        try:
            definition, loss = _legacy_loss(mode)
            async with engine.begin() as connection:
                legacy = await connection.run_sync(_reflect_legacy)
                await _seed_legacy(connection, legacy, definition, loss)
                before = await _capture_legacy(connection, legacy)
            upgraded = await asyncio.to_thread(_migrate, scratch_database, "upgrade", "head")
            assert upgraded.returncode == 0, upgraded.stderr
            async with engine.connect() as connection:
                await _assert_legacy_unchanged(connection, before)
            roots = PostgresStrategyStore(engine)
            store = PostgresExecutionStore(engine)
            created = (await store.get_deployment(loss.deployment.id)).deployment
            assert created.cash == loss.deployment.cash
            assert set((await store.get_deployment(created.id)).fills) == {loss.fills[0]}
            deleted = await roots.delete(definition.strategy_id)
            assert deleted.counts.paper_deployments == 0
            detached = (await PostgresExecutionStore(engine).get_deployment(created.id)).deployment
            assert detached.strategy_id is None
            assert detached.strategy_name == definition.name
            assert detached.strategy_fingerprint == loss.deployment.strategy_fingerprint
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
        finally:
            await dispose(engine)

    asyncio.run(scenario())


def test_risk0065_downgrade_without_detached_paper_is_lossless(scratch_database: str) -> None:
    """0064→risk0065→0064 retains seeded rows and restores the original identity check."""
    old = _migrate(scratch_database, "upgrade", "0064")
    assert old.returncode == 0, old.stderr

    async def check() -> None:
        """Use only legacy SQL so unrelated future HEAD downgrades cannot mask this check."""
        engine = create_engine(SecretStr(scratch_database))
        try:
            definition, loss = _legacy_loss(DeploymentMode.PAPER)
            async with engine.begin() as connection:
                legacy = await connection.run_sync(_reflect_legacy)
                await _seed_legacy(connection, legacy, definition, loss)
                before = await _capture_legacy(connection, legacy)
                constraint_before = await _identity_constraint(connection)
            upgraded = await asyncio.to_thread(_migrate, scratch_database, "upgrade", "risk0065")
            assert upgraded.returncode == 0, upgraded.stderr
            downgraded = await asyncio.to_thread(_migrate, scratch_database, "downgrade", "0064")
            assert downgraded.returncode == 0, downgraded.stderr
            async with engine.connect() as connection:
                await _assert_legacy_unchanged(connection, before)
                assert await _identity_constraint(connection) == constraint_before
                revision = (
                    await connection.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
                assert revision == "0064"
        finally:
            await dispose(engine)

    asyncio.run(check())


async def _identity_constraint(connection: AsyncConnection) -> str:
    """Read the actual deployment identity check, not current Python metadata."""
    value = (
        await connection.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'deployments'::regclass "
                "AND conname = 'ck_deployments_kind_identity'"
            )
        )
    ).scalar_one()
    assert isinstance(value, str)
    return value


def test_risk0065_downgrade_refuses_detached_paper_evidence(scratch_database: str) -> None:
    """The isolated risk revision allows paper detach and refuses evidence-destroying rollback."""
    old = _migrate(scratch_database, "upgrade", "0064")
    assert old.returncode == 0, old.stderr

    async def check() -> None:
        """Prove the widened FK detach and rollback guard using only the legacy projection."""
        engine = create_engine(SecretStr(scratch_database))
        try:
            definition, loss = _legacy_loss(DeploymentMode.PAPER)
            async with engine.begin() as connection:
                legacy = await connection.run_sync(_reflect_legacy)
                await _seed_legacy(connection, legacy, definition, loss)
            upgraded = await asyncio.to_thread(_migrate, scratch_database, "upgrade", "risk0065")
            assert upgraded.returncode == 0, upgraded.stderr
            async with engine.begin() as connection:
                roots = legacy.tables["strategies"]
                await connection.execute(
                    roots.delete().where(roots.c.strategy_id == str(definition.strategy_id))
                )
                books = legacy.tables["deployments"]
                assert (await connection.execute(select(books.c.strategy_id))).scalar_one() is None
                before = await _capture_legacy(connection, legacy)
            refused = await asyncio.to_thread(_migrate, scratch_database, "downgrade", "0064")
            assert refused.returncode != 0
            assert "detached paper risk evidence exists" in refused.stderr
            async with engine.connect() as connection:
                await _assert_legacy_unchanged(connection, before)
                revision = (
                    await connection.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
                assert revision == "risk0065"
        finally:
            await dispose(engine)

    asyncio.run(check())
