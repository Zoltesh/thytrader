"""Published and active risk policy tables."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from thytrader.persistence.schema_metadata import metadata

published_risk_policies = Table(
    "published_risk_policies",
    metadata,
    Column("policy_fingerprint", String(71), primary_key=True),
    Column("policy_id", String(36), nullable=False),
    Column("version", Integer(), nullable=False),
    Column("canonical_definition", Text(), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("policy_id", "version", name="ux_published_risk_policy_identity_version"),
    CheckConstraint("version > 0", name="ck_published_risk_policy_version_positive"),
    CheckConstraint(
        "policy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_published_risk_policy_fingerprint_format",
    ),
)

active_risk_policy = Table(
    "active_risk_policy",
    metadata,
    Column("id", Integer(), primary_key=True),
    Column("policy_fingerprint", String(71), nullable=False),
    Column("activated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["policy_fingerprint"],
        ["published_risk_policies.policy_fingerprint"],
        ondelete="RESTRICT",
    ),
    CheckConstraint("id = 1", name="ck_active_risk_policy_singleton"),
)

__all__ = [
    "active_risk_policy",
    "published_risk_policies",
]
