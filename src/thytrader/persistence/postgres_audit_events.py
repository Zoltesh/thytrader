"""PostgreSQL implementation of the append-only audit event store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import desc, insert, select
from sqlalchemy.exc import SQLAlchemyError

from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventUnavailableError,
)
from thytrader.persistence.schema import audit_events

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


class PostgresAuditEventStore:
    """Durable append-only audit event repository backed by PostgreSQL."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the store to a managed async engine."""
        self._engine = engine

    async def append(self, event: AuditEvent) -> None:
        """Insert one audit event record."""
        category_str = (
            event.category.value if hasattr(event.category, "value") else str(event.category)
        )
        outcome_str = event.outcome.value if hasattr(event.outcome, "value") else str(event.outcome)
        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    insert(audit_events).values(
                        id=event.id,
                        occurred_at=event.occurred_at,
                        category=category_str,
                        action=event.action,
                        outcome=outcome_str,
                        provider=event.provider,
                        product_id=event.product_id,
                        detail=event.detail,
                        recorded_at=event.recorded_at,
                    )
                )
        except SQLAlchemyError as err:
            raise AuditEventUnavailableError("Audit event storage is unavailable.") from err

    async def list_recent(self, *, limit: int = 50) -> tuple[AuditEvent, ...]:
        """Return newest-first audit events bounded by limit."""
        if limit < 1:
            raise ValueError("limit must be positive")
        bounded_limit = min(limit, 500)
        stmt = (
            select(
                audit_events.c.id,
                audit_events.c.occurred_at,
                audit_events.c.category,
                audit_events.c.action,
                audit_events.c.outcome,
                audit_events.c.provider,
                audit_events.c.product_id,
                audit_events.c.detail,
                audit_events.c.recorded_at,
            )
            .order_by(desc(audit_events.c.occurred_at), desc(audit_events.c.id))
            .limit(bounded_limit)
        )
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(stmt)).all()
        except SQLAlchemyError as err:
            raise AuditEventUnavailableError("Audit event storage is unavailable.") from err

        return tuple(
            AuditEvent(
                id=row.id,
                occurred_at=row.occurred_at,
                category=AuditEventCategory(row.category),
                action=row.action,
                outcome=AuditEventOutcome(row.outcome),
                provider=row.provider,
                product_id=row.product_id,
                detail=row.detail,
                recorded_at=row.recorded_at,
            )
            for row in rows
        )
