"""Append-only audit event tables."""

from __future__ import annotations

from sqlalchemy import (
    UUID,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    String,
    Table,
    Text,
)

from thytrader.persistence.schema_metadata import metadata

audit_events = Table(
    "audit_events",
    metadata,
    # Column comments live here, not in the database: the creating migration set none,
    # and ``alembic check`` (CI) requires metadata to match the migrated schema.
    Column("id", UUID(), primary_key=True),  # Surrogate UUID identifier.
    Column("occurred_at", DateTime(timezone=True), nullable=False),  # Event occurrence instant.
    Column("category", String(32), nullable=False),  # Functional event category.
    Column("action", String(64), nullable=False),  # Action or transition name.
    Column("outcome", String(16), nullable=False),  # Success, failure, or info.
    Column("provider", String(32), nullable=True),  # Optional exchange/service provider.
    Column("product_id", String(32), nullable=True),  # Optional product symbol.
    Column("detail", Text(), nullable=False, server_default=""),  # Redacted details.
    Column(
        "recorded_at",
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
    ),  # Row insertion instant.
    CheckConstraint(
        "category IN ("
        "'connection', 'snapshot', 'worker_error', 'market_data', 'websocket', "
        "'research', 'runtime', 'memory'"
        ")",
        name="ck_audit_events_category",
    ),
    CheckConstraint(
        "outcome IN ('success', 'failure', 'info')",
        name="ck_audit_events_outcome",
    ),
)

Index(
    "ix_audit_events_occurred_at_desc",
    audit_events.c.occurred_at.desc(),
    audit_events.c.id.desc(),
)

Index(
    "ix_audit_events_category_occurred_at_desc",
    audit_events.c.category,
    audit_events.c.occurred_at.desc(),
)

__all__ = [
    "audit_events",
]
