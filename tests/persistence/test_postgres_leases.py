"""PostgreSQL lease, revision, capital, and STOPPED residual occupancy."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from uuid import uuid4

from pydantic import SecretStr
import pytest

from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    ExecutionConflictError,
    LifecycleCommand,
    Position,
    RuntimePhase,
)
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.risk.exposure import risk_bearing_snapshots, snapshot_has_residual_exposure

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
_NOW = datetime(2026, 1, 2, 15, tzinfo=UTC)

pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


def _discretionary(*, status: DeploymentStatus = DeploymentStatus.RUNNING) -> Deployment:
    """Return a paper discretionary book that does not need a published fingerprint."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint=None,
        strategy_id=None,
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=status,
        kind=DeploymentKind.DISCRETIONARY,
        timeframe="1h",
        cash=Decimal("10000"),
        phase=RuntimePhase.FLAT,
        created_at=_NOW,
        updated_at=_NOW,
        paper_starting_cash=Decimal("10000"),
        paper_maker_fee_rate=Decimal("0.001"),
        paper_taker_fee_rate=Decimal("0.002"),
        initial_equity=Decimal("10000"),
        baseline_equity=Decimal("10000"),
        utc_day_open_equity=Decimal("10000"),
        utc_day_open_at=_NOW,
        high_water_mark_equity=Decimal("10000"),
        allocated_capital=Decimal("4000"),
    )


@pytest.mark.anyio
async def test_postgres_lease_and_revision_fence() -> None:
    """F08: exclusive lease, expiry takeover, and revision-checked pause."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    engine = create_engine(SecretStr(_TEST_DATABASE_URL))
    store = PostgresExecutionStore(engine)
    created = await store.create_deployment(_discretionary())
    try:
        first = await store.acquire_worker_lease(
            created.id, holder="worker-a", now=_NOW, ttl=timedelta(seconds=45)
        )
        blocked = await store.acquire_worker_lease(
            created.id,
            holder="worker-b",
            now=_NOW + timedelta(seconds=5),
            ttl=timedelta(seconds=45),
        )
        taken = await store.acquire_worker_lease(
            created.id,
            holder="worker-b",
            now=_NOW + timedelta(seconds=50),
            ttl=timedelta(seconds=45),
        )
        assert first is not None
        assert blocked is None
        assert taken is not None
        current = await store.get_deployment(created.id)
        paused = replace(
            current.deployment,
            status=DeploymentStatus.PAUSED,
            lifecycle_command=LifecycleCommand.STOP_NEW_ENTRIES,
        )
        saved = await store.save_deployment(paused, expected_revision=paused.revision)
        stale = replace(current.deployment, status=DeploymentStatus.RUNNING, cash=Decimal("1"))
        with pytest.raises(ExecutionConflictError, match="revision"):
            await store.save_deployment(stale, expected_revision=current.deployment.revision)
        loaded = await store.get_deployment(created.id)
        assert loaded.deployment.status is DeploymentStatus.PAUSED
        assert loaded.deployment.revision == saved.revision
        assert loaded.deployment.cash == Decimal("10000")
        assert loaded.deployment.allocated_capital == Decimal("4000")
        assert loaded.deployment.initial_equity == Decimal("10000")
    finally:
        await dispose(engine)


@pytest.mark.anyio
async def test_postgres_stopped_residual_and_live_capital_columns() -> None:
    """F09/F12: STOPPED inventory stays risk-bearing; venue quote is not cash."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    engine = create_engine(SecretStr(_TEST_DATABASE_URL))
    store = PostgresExecutionStore(engine)
    created = await store.create_deployment(_discretionary())
    try:
        position = Position(
            deployment_id=created.id,
            quantity=Decimal("0.01"),
            entry_price=Decimal("100"),
            stop_price=Decimal("90"),
            target_price=Decimal("120"),
            entered_bar=_NOW,
            updated_at=_NOW,
            product_id="BTC-USD",
        )
        await store.save_position(position, deployment_id=created.id)
        stopped = replace(
            created,
            status=DeploymentStatus.STOPPED,
            lifecycle_command=LifecycleCommand.MANAGED_SHUTDOWN,
            phase=RuntimePhase.OPEN,
            venue_available_quote=Decimal("88888"),
            cash=Decimal("10000"),
        )
        await store.save_deployment(stopped, expected_revision=created.revision)
        loaded = await store.get_deployment(created.id)
        assert loaded.deployment.venue_available_quote == Decimal("88888")
        assert loaded.deployment.cash == Decimal("10000")
        assert snapshot_has_residual_exposure(loaded) is True
        bearing = risk_bearing_snapshots((loaded,), DeploymentMode.PAPER)
        assert loaded in bearing
    finally:
        await dispose(engine)
