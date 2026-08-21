"""Live PostgreSQL tests for append-only audit event persistence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy.exc import SQLAlchemyError

from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventUnavailableError,
)
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_audit_events import PostgresAuditEventStore

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")


@pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)
def test_postgres_audit_event_append_and_list_recent() -> None:
    """Postgres store appends valid events and queries newest-first bounded list."""

    async def exercise() -> None:
        if _TEST_DATABASE_URL is None:
            raise AssertionError("PostgreSQL integration URL was not configured.")
        engine = create_engine(SecretStr(_TEST_DATABASE_URL))
        store = PostgresAuditEventStore(engine)

        try:
            now = datetime.now(UTC)
            event_id = uuid4()
            event = AuditEvent(
                id=event_id,
                occurred_at=now,
                category=AuditEventCategory.SNAPSHOT,
                action="portfolio_snapshot_persisted",
                outcome=AuditEventOutcome.SUCCESS,
                provider="coinbase",
                product_id=None,
                detail="Test detail line",
                recorded_at=now,
            )

            await store.append(event)

            recent = await store.list_recent(limit=10)
            assert len(recent) >= 1
            matching = [e for e in recent if e.id == event_id]
            assert len(matching) == 1
            loaded = matching[0]
            assert loaded.category == AuditEventCategory.SNAPSHOT
            assert loaded.action == "portfolio_snapshot_persisted"
            assert loaded.outcome == AuditEventOutcome.SUCCESS
            assert loaded.provider == "coinbase"
            assert loaded.detail == "Test detail line"
            assert loaded.occurred_at == now
        finally:
            await dispose(engine)

    asyncio.run(exercise())


def test_postgres_audit_event_store_handles_engine_error() -> None:
    """Store wraps SQLAlchemyError into typed AuditEventUnavailableError."""
    mock_engine = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.execute.side_effect = SQLAlchemyError("DB error")
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    mock_engine.connect.return_value = mock_ctx
    mock_engine.begin.return_value = mock_ctx

    store = PostgresAuditEventStore(mock_engine)

    with pytest.raises(AuditEventUnavailableError):
        asyncio.run(
            store.append(
                AuditEvent(
                    occurred_at=datetime.now(UTC),
                    category=AuditEventCategory.CONNECTION,
                    action="test",
                    outcome=AuditEventOutcome.INFO,
                )
            )
        )

    with pytest.raises(AuditEventUnavailableError):
        asyncio.run(store.list_recent(limit=10))
