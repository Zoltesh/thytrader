"""PostgreSQL paper futures books: migration 0073, bindings and funding (ADR 0129 §4)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import SecretStr
import pytest

from tests.execution.test_paper_futures_books import _CONTRACT
from tests.persistence.test_migration_0048_strategy_root import _alembic, scratch_database
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_futures_books import PostgresFuturesContractStore
from thytrader.trading.futures_book import BoundFuturesContract, FuturesBookUnavailableError
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    ExecutionConflictError,
    FundingCashFlow,
    RuntimePhase,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncEngine

__all__ = ["scratch_database"]
_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)
_NOW = datetime(2026, 10, 10, 12, tzinfo=UTC)
_PERP = "BIP-20DEC30-CDE"


@pytest.mark.anyio
async def test_0073_round_trips(scratch_database: str) -> None:
    """Upgrade, downgrade to 0072 and upgrade again on a scratch database."""
    assert _alembic(scratch_database, "head").returncode == 0
    assert _alembic(scratch_database, "0072", operation="downgrade").returncode == 0
    assert _alembic(scratch_database, "head").returncode == 0


def _run(body: Callable[[AsyncEngine], Awaitable[None]]) -> None:
    """Run one async body against the throwaway test database."""

    async def exercise() -> None:
        if _TEST_DATABASE_URL is None:
            raise AssertionError("PostgreSQL integration URL was not configured.")
        engine = create_engine(SecretStr(_TEST_DATABASE_URL))
        try:
            await body(engine)
        finally:
            await dispose(engine)

    asyncio.run(exercise())


def _deployment() -> Deployment:
    """One running paper futures book with 10000 USD (no strategy snapshot needed)."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint=None,
        strategy_id=None,
        kind=DeploymentKind.DISCRETIONARY,
        product_id=_PERP,
        timeframe="1h",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=Decimal(10000),
        paper_maker_fee_rate=Decimal(0),
        paper_taker_fee_rate=Decimal("0.0005"),
        cash=Decimal(10000),
        initial_equity=Decimal(10000),
        phase=RuntimePhase.FLAT,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _flow(deployment: Deployment, hours: int, amount: str) -> FundingCashFlow:
    """One funding hour."""
    return FundingCashFlow(
        deployment_id=deployment.id,
        product_id=_PERP,
        funding_time=_NOW + timedelta(hours=hours),
        signed_quantity=Decimal("0.5"),
        mark_price=Decimal(100),
        rate=Decimal("0.0001"),
        amount=Decimal(amount),
        applied_at=_NOW + timedelta(hours=hours, minutes=70),
    )


def test_a_contract_binds_once_and_reads_back_exactly() -> None:
    """The binding round-trips; a second binding for the deployment is refused."""

    async def body(engine: AsyncEngine) -> None:
        execution = PostgresExecutionStore(engine)
        deployment = await execution.create_deployment(_deployment())
        contracts = PostgresFuturesContractStore(engine)
        binding = BoundFuturesContract(
            deployment_id=deployment.id,
            contract=_CONTRACT,
            fee_per_contract=Decimal("0.15"),
            bound_at=_NOW,
        )
        await contracts.bind_contract(binding)
        assert await contracts.load_contract(deployment.id) == binding
        assert await contracts.load_contract(uuid4()) is None
        with pytest.raises(FuturesBookUnavailableError, match="already has"):
            await contracts.bind_contract(replace(binding, fee_per_contract=Decimal(1)))

    _run(body)


def test_funding_moves_cash_once_under_the_row_lock() -> None:
    """Applied hours land in the snapshot and cash; a replay or stale revision changes nothing."""

    async def body(engine: AsyncEngine) -> None:
        execution = PostgresExecutionStore(engine)
        deployment = await execution.create_deployment(_deployment())
        before = await execution.get_deployment(deployment.id)
        flows = (_flow(deployment, 1, "-0.005"), _flow(deployment, 2, "-0.004"))
        applied, after = await execution.apply_funding_transaction(
            deployment.id, flows=flows, expected_revision=before.deployment.revision
        )
        assert applied == 2
        assert after.deployment.cash == Decimal("9999.991")
        assert after.deployment.revision == before.deployment.revision + 1
        assert after.funding == flows
        replayed, again = await execution.apply_funding_transaction(deployment.id, flows=flows)
        assert replayed == 0
        assert again.deployment.cash == Decimal("9999.991")
        with pytest.raises(ExecutionConflictError):
            await execution.apply_funding_transaction(
                deployment.id,
                flows=(_flow(deployment, 3, "-1"),),
                expected_revision=before.deployment.revision,
            )
        reloaded = await execution.get_deployment(deployment.id)
        assert reloaded.funding == flows

    _run(body)
