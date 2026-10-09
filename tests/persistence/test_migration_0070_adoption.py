"""Migration 0070 admits adoption rows, and its downgrade refuses while any exist (ADR 0124)."""

from __future__ import annotations

import os

from pydantic import SecretStr
import pytest
from sqlalchemy import text

from tests.adoption_support import adopted_book, live_book, records_for
from tests.persistence.test_migration_0048_strategy_root import _alembic, scratch_database
from thytrader.memory.recording import _record_from_submit
from thytrader.memory.store import InMemoryExperientialMemoryStore
from thytrader.memory.trade_reason_scope import TradeReasonScope
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_memory import PostgresExperientialMemoryStore
from thytrader.trading.models import (
    DeploymentKind,
    ExecutionStoreError,
    IntentPurpose,
    OrderKind,
    OrderStatus,
    RuntimePhase,
)

__all__ = ["scratch_database"]
pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="An isolated PostgreSQL test database is required.",
)

_FP = "sha256:" + "e" * 64
_DELETE_ADOPTIONS = (
    "DELETE FROM trade_reason_records WHERE purpose = 'adoption'",
    "DELETE FROM execution_fills WHERE order_id IN "
    "(SELECT id FROM execution_orders WHERE kind = 'adoption')",
    "DELETE FROM execution_orders WHERE kind = 'adoption'",
    "DELETE FROM order_intents WHERE kind = 'adoption'",
)


def _scope() -> TradeReasonScope:
    """Attribution for one discretionary adoption why-trade row."""
    return TradeReasonScope(
        store=InMemoryExperientialMemoryStore(),
        policy_fingerprint=_FP,
        policy_source="published",
        risk_decision="allow",
        risk_reason_code="ALLOWED",
        risk_detail="Admitted.",
    )


@pytest.mark.anyio
async def test_adoption_rows_persist_and_block_the_0070_downgrade(scratch_database: str) -> None:
    """Adoption intents, orders, fills and why-trade rows survive a reload at head."""
    migrated = _alembic(scratch_database, "head")
    assert migrated.returncode == 0, migrated.stderr
    engine = create_engine(SecretStr(scratch_database))
    try:
        adopted = await adopted_book(
            PostgresExecutionStore(engine), book=live_book(kind=DeploymentKind.DISCRETIONARY)
        )
        book_id = adopted.deployment.id
        reloaded = await PostgresExecutionStore(engine).get_deployment(book_id)
        assert [intent.purpose for intent in reloaded.intents] == [IntentPurpose.ADOPTION]
        assert [intent.kind for intent in reloaded.intents] == [OrderKind.ADOPTION]
        order = reloaded.orders[0]
        assert order.kind is OrderKind.ADOPTION and order.status is OrderStatus.FILLED
        assert order.venue_order_id is None and order.venue_observed_at is None
        assert reloaded.fills[0].economics_applied_at is not None
        assert reloaded.position is not None and reloaded.deployment.phase is RuntimePhase.OPEN
        record = _record_from_submit(intent=reloaded.intents[0], snapshot=reloaded, scope=_scope())
        await PostgresExperientialMemoryStore(engine).append_trade_reason(record)
    finally:
        await dispose(engine)
    refused = _alembic(scratch_database, "0069", operation="downgrade")
    assert refused.returncode != 0
    assert "Cannot downgrade 0070" in refused.stderr


@pytest.mark.anyio
async def test_0070_downgrade_restores_the_narrower_checks_once_no_adoption_exists(
    scratch_database: str,
) -> None:
    """Without adoption rows the downgrade runs, 0069 refuses adoption, and head admits it."""
    migrated = _alembic(scratch_database, "head")
    assert migrated.returncode == 0, migrated.stderr
    engine = create_engine(SecretStr(scratch_database))
    try:
        adopted = await adopted_book(
            PostgresExecutionStore(engine), book=live_book(kind=DeploymentKind.DISCRETIONARY)
        )
        async with engine.begin() as connection:
            for statement in _DELETE_ADOPTIONS:
                await connection.execute(text(statement))
    finally:
        await dispose(engine)
    downgraded = _alembic(scratch_database, "0069", operation="downgrade")
    assert downgraded.returncode == 0, downgraded.stderr
    retry = records_for(adopted.deployment)
    engine = create_engine(SecretStr(scratch_database))
    try:
        with pytest.raises(ExecutionStoreError):
            await PostgresExecutionStore(engine).save_intent(retry.intent)
    finally:
        await dispose(engine)
    upgraded = _alembic(scratch_database, "head")
    assert upgraded.returncode == 0, upgraded.stderr
    engine = create_engine(SecretStr(scratch_database))
    try:
        saved = await PostgresExecutionStore(engine).save_intent(retry.intent)
        assert saved.kind is OrderKind.ADOPTION
    finally:
        await dispose(engine)
