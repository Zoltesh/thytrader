"""Strategy registry, snapshot, and dataset-binding tables."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Table,
    Text,
)

from thytrader.persistence.schema_metadata import FINGERPRINT_REGEX, metadata

strategies = Table(
    "strategies",
    metadata,
    Column("strategy_id", String(36), primary_key=True, comment="UUIDv7 strategy identity."),
    Column("name", String(120), nullable=False),
    Column("product_id", String(32), nullable=True, comment="Primary product when parseable."),
    Column("timeframe", String(8), nullable=True, comment="Decision clock when parseable."),
    Column("document", Text(), nullable=False, comment="Canonical JSON when valid, else sorted."),
    Column("is_valid", Boolean(), nullable=False),
    Column("validation_issues", Text(), nullable=False, server_default="[]"),
    Column("current_fingerprint", String(71), nullable=True),
    Column("revision", BigInteger(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("revision > 0", name="ck_strategies_revision_positive"),
    CheckConstraint(
        "(is_valid AND current_fingerprint IS NOT NULL) "
        "OR (NOT is_valid AND current_fingerprint IS NULL)",
        name="ck_strategies_validity_fingerprint",
    ),
    CheckConstraint(
        f"current_fingerprint IS NULL OR current_fingerprint ~ {FINGERPRINT_REGEX}",
        name="ck_strategies_current_fingerprint_format",
    ),
)

Index(
    "ix_strategies_updated",
    strategies.c.updated_at.desc(),
    strategies.c.strategy_id.asc(),
)

strategy_snapshots = Table(
    "strategy_snapshots",
    metadata,
    Column("strategy_fingerprint", String(71), primary_key=True),
    Column(
        "strategy_id",
        String(36),
        nullable=True,
        comment="Owning strategy; NULL only for snapshots a kept live deployment ran.",
    ),
    Column("canonical_definition", Text(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["strategy_id"],
        ["strategies.strategy_id"],
        ondelete="SET NULL",
        name="fk_strategy_snapshots_strategy_id",
    ),
    CheckConstraint(
        f"strategy_fingerprint ~ {FINGERPRINT_REGEX}",
        name="ck_strategy_snapshots_fingerprint_format",
    ),
)

Index("ix_strategy_snapshots_strategy_id", strategy_snapshots.c.strategy_id)

strategy_dataset_bindings = Table(
    "strategy_dataset_bindings",
    metadata,
    Column("strategy_fingerprint", String(71), primary_key=True),
    Column("dataset_fingerprint", String(71), primary_key=True),
    Column("strategy_id", String(36), nullable=False),
    Column("bound_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["strategy_fingerprint"],
        ["strategy_snapshots.strategy_fingerprint"],
        name="fk_strategy_dataset_bindings_snapshot",
    ),
    ForeignKeyConstraint(
        ["strategy_id"],
        ["strategies.strategy_id"],
        ondelete="CASCADE",
        name="fk_strategy_dataset_bindings_strategy_id",
    ),
    CheckConstraint(
        f"strategy_fingerprint ~ {FINGERPRINT_REGEX}",
        name="ck_strategy_dataset_binding_strategy_fingerprint_format",
    ),
    CheckConstraint(
        f"dataset_fingerprint ~ {FINGERPRINT_REGEX}",
        name="ck_strategy_dataset_binding_dataset_fingerprint_format",
    ),
)

Index("ix_strategy_dataset_bindings_strategy_id", strategy_dataset_bindings.c.strategy_id)

Index(
    "ix_strategy_dataset_bindings_dataset_fingerprint",
    strategy_dataset_bindings.c.dataset_fingerprint,
)

__all__ = [
    "strategies",
    "strategy_dataset_bindings",
    "strategy_snapshots",
]
