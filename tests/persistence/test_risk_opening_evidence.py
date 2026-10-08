"""Qualified openings survive real PostgreSQL reload without rewriting legacy economics."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
import os
from pathlib import Path
from typing import TYPE_CHECKING

from alembic.config import Config
from pydantic import SecretStr
import pytest
from sqlalchemy import select

from alembic import command
from tests.persistence.test_migration_0048_strategy_root import scratch_database
from tests.risk.test_safety_evidence import _MIDNIGHT, _TODAY, MidnightProvider, overnight_long
from thytrader.execution.capital import refresh_performance
from thytrader.market_data.service import MarketDataService
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore, _deployment_values
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.persistence.schema import deployments
from thytrader.risk.accounting_evidence import accounting_snapshot, risk_market_data_scope
from thytrader.risk.breakers import _daily_pnl
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.trading.models import IntentPurpose, OrderIntent
from thytrader.trading.overlay import InstrumentScopedStore

if TYPE_CHECKING:
    from uuid import UUID

__all__ = ["scratch_database"]
_ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="An explicitly supplied hermetic test database is required, never production.",
)


def _migrate(database_url: str, target: str, *, downgrade: bool = False) -> None:
    """Run only the requested test migration, optionally using lead-provided private scripts."""
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", os.getenv("THYTRADER_TEST_MIGRATIONS_DIR", str(_ROOT / "alembic"))
    )
    prior = os.environ.get("THYTRADER_DATABASE_URL")
    os.environ["THYTRADER_DATABASE_URL"] = database_url
    try:
        if downgrade:
            command.downgrade(config, target)
        else:
            command.upgrade(config, target)
    finally:
        if prior is None:
            os.environ.pop("THYTRADER_DATABASE_URL", None)
        else:
            os.environ["THYTRADER_DATABASE_URL"] = prior


@pytest.mark.anyio
async def test_no_backfill_scoped_save_restart_and_safe_downgrade(scratch_database: str) -> None:
    """0069 adds no invented baseline; qualified marks and exact cash survive a fresh store."""
    await asyncio.to_thread(_migrate, scratch_database, "0068")
    engine = create_engine(SecretStr(scratch_database))
    deployment_id: UUID
    try:
        strategies = PostgresStrategyStore(engine)
        strategy = await create_strategy_from_definition(strategies, create_template_strategy())
        bound = await strategies.snapshot(strategy.strategy_id)
        full = overnight_long()
        root = replace(
            full.deployment,
            strategy_id=strategy.strategy_id,
            strategy_fingerprint=bound.strategy_fingerprint,
            daily_loss_latched=True,
            drawdown_latched=True,
            paper_maker_fee_rate=Decimal("0"),
            paper_taker_fee_rate=Decimal("0"),
            timeframe="1h",
        )
        values = _deployment_values(root)
        values.pop("risk_day_open_evidence")
        async with engine.begin() as connection:
            await connection.execute(deployments.insert().values(**values))
        deployment_id = root.id
    finally:
        await dispose(engine)
    await asyncio.to_thread(_migrate, scratch_database, "0069")
    engine = create_engine(SecretStr(scratch_database))
    try:
        async with engine.connect() as connection:
            raw = (
                (
                    await connection.execute(
                        select(deployments).where(deployments.c.id == deployment_id)
                    )
                )
                .mappings()
                .one()
            )
            assert raw["risk_day_open_evidence"] is None
            assert raw["utc_day_open_equity"] == "9950"
            assert raw["utc_day_open_at"] == _MIDNIGHT
            assert raw["cash"] == "9900"
        store = PostgresExecutionStore(engine)
        for order in full.orders:
            await store.save_intent(
                OrderIntent(
                    id=order.intent_id,
                    deployment_id=order.deployment_id,
                    client_order_id=order.client_order_id,
                    purpose=IntentPurpose.ENTRY,
                    side=order.side,
                    kind=order.kind,
                    quantity=order.quantity,
                    created_at=order.created_at,
                    candle_starts_at=order.created_at,
                    price=order.price,
                    product_id=order.product_id,
                )
            )
            await store.save_order(order)
        for fill in full.fills:
            await store.save_fill(fill)
        await store.save_position(full.position, deployment_id=deployment_id)
        scoped = InstrumentScopedStore(store, "ETH-USD")
        with risk_market_data_scope(MarketDataService(MidnightProvider())):
            accounting = await accounting_snapshot(scoped, deployment_id, as_of=_TODAY)
        assert accounting.accounting_complete and accounting.fills == full.fills
        updated = refresh_performance(accounting, marks={"BTC-USD": Decimal("50")}, now=_TODAY)
        focused = await scoped.get_deployment(deployment_id)
        await scoped.save_deployment(
            replace(
                focused.deployment,
                risk_day_open_evidence=updated.risk_day_open_evidence,
                performance_capital_quote=updated.performance_capital_quote,
                performance_maximum_drawdown_fraction=updated.performance_maximum_drawdown_fraction,
            )
        )
    finally:
        await dispose(engine)
    engine = create_engine(SecretStr(scratch_database))
    try:
        restarted = await PostgresExecutionStore(engine).get_accounting_snapshot(deployment_id)
        assert restarted.deployment.cash == Decimal("9900")
        assert restarted.deployment.utc_day_open_equity == Decimal("9950")
        assert restarted.deployment.utc_day_open_at == _MIDNIGHT
        assert restarted.deployment.daily_loss_latched and restarted.deployment.drawdown_latched
        assert restarted.fills == full.fills
        evidence = restarted.deployment.risk_day_open_evidence
        assert evidence is not None and evidence.equity == Decimal("10000")
        assert evidence.marks[0].price == Decimal("100")
        assert _daily_pnl(restarted, marks={"BTC-USD": Decimal("50")}, as_of=_TODAY) == Decimal(
            "-50"
        )
    finally:
        await dispose(engine)
    with pytest.raises(RuntimeError, match="qualified risk opening evidence"):
        await asyncio.to_thread(_migrate, scratch_database, "0068", downgrade=True)
