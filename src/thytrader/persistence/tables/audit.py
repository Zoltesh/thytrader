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
    Column("id", UUID(), primary_key=True, comment="Surrogate UUID identifier."),
    Column(
        "occurred_at",
        DateTime(timezone=True),
        nullable=False,
        comment="Event occurrence instant.",
    ),
    Column("category", String(32), nullable=False, comment="Functional event category."),
    Column("action", String(64), nullable=False, comment="Action or transition name."),
    Column("outcome", String(16), nullable=False, comment="Success, failure, or info."),
    Column("provider", String(32), nullable=True, comment="Optional exchange/service provider."),
    Column("product_id", String(32), nullable=True, comment="Optional product symbol."),
    Column(
        "detail",
        Text(),
        nullable=False,
        server_default="",
        comment="Redacted descriptive details.",
    ),
    Column(
        "recorded_at",
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
        comment="Row insertion instant.",
    ),
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
