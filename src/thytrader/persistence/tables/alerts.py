"""Operator safety alert tables."""

from __future__ import annotations

from sqlalchemy import (
    UUID,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
)

from thytrader.persistence.schema_metadata import metadata

operator_alert_checks = Table(
    "operator_alert_checks",
    metadata,
    Column("code", String(48), primary_key=True),
    Column("subject", String(128), primary_key=True),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("failed", Boolean(), nullable=False),
    comment="Persistent monotone check watermarks, including verified healthy observations.",
)

operator_alerts = Table(
    "operator_alerts",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("code", String(48), nullable=False, comment="Stable alert reason code (ADR 0115)."),
    Column("scope", String(16), nullable=False, comment="deployment or worker scope."),
    Column("subject", String(128), nullable=False, comment="Deployment id or worker identity."),
    Column(
        "deployment_id",
        UUID(),
        nullable=True,
        comment="Owning book when the alert is deployment-scoped.",
    ),
    Column("product_id", String(32), nullable=True),
    Column("severity", String(12), nullable=False),
    Column("detail", String(500), nullable=False, comment="Redacted operator-facing summary."),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column(
        "occurrences",
        Integer(),
        nullable=False,
        comment="Consecutive supervision cycles that re-observed this open alert.",
    ),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    Column("resolution_detail", String(500), nullable=False, server_default=""),
    Column("delivery_provider", String(16), nullable=False, server_default="none"),
    Column("delivery_status", String(16), nullable=False, server_default="pending"),
    Column("delivery_attempts", Integer(), nullable=False, server_default="0"),
    Column("delivery_detail", String(500), nullable=False, server_default=""),
    Column("delivery_token", UUID(as_uuid=True), nullable=True),
    Column("delivery_expires_at", DateTime(timezone=True), nullable=True),
    ForeignKeyConstraint(
        ["deployment_id"],
        ["deployments.id"],
        ondelete="SET NULL",
        name="fk_operator_alerts_deployment_id",
    ),
    CheckConstraint(
        "severity IN ('info', 'warning', 'critical')", name="ck_operator_alerts_severity"
    ),
    CheckConstraint(
        "delivery_status IN ('pending', 'skipped', 'logged', 'delivered', 'failed', 'exhausted')",
        name="ck_operator_alerts_delivery_status",
    ),
    CheckConstraint("occurrences >= 1", name="ck_operator_alerts_occurrences_positive"),
    comment="Durable deduplicated operator safety alerts (ADR 0115).",
)

Index(
    "ux_operator_alerts_open",
    operator_alerts.c.code,
    operator_alerts.c.subject,
    unique=True,
    postgresql_where=operator_alerts.c.resolved_at.is_(None),
)

Index(
    "ix_operator_alerts_last_seen",
    operator_alerts.c.last_seen_at,
)

__all__ = [
    "operator_alert_checks",
    "operator_alerts",
]
