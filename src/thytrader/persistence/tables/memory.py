"""Experiential memory tables (journal, sentiment, patterns, notifications, models)."""

from __future__ import annotations

from sqlalchemy import (
    UUID,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from thytrader.persistence.schema_metadata import metadata

experiential_journal_entries = Table(
    "experiential_journal_entries",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("origin", String(8), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("title", String(200), nullable=False),
    Column("body", Text(), nullable=False),
    Column("evidence_kind", String(16), nullable=False),
    Column("evidence_id", String(128), nullable=True),
    Column("product_id", String(32), nullable=True),
    Column("runtime_mode", String(16), nullable=False),
    Column("lesson_outcome", String(16), nullable=False),
    CheckConstraint("origin IN ('human', 'agent')", name="ck_experiential_journal_origin"),
    CheckConstraint(
        "kind IN ('fact', 'lesson', 'note')",
        name="ck_experiential_journal_kind",
    ),
    CheckConstraint(
        "evidence_kind IN ("
        "'none', 'backtest', 'paper_fill', 'live_fill', 'deployment', 'research', 'market_data'"
        ")",
        name="ck_experiential_journal_evidence_kind",
    ),
    CheckConstraint(
        "runtime_mode IN ('none', 'research', 'paper', 'live')",
        name="ck_experiential_journal_runtime_mode",
    ),
    CheckConstraint(
        "lesson_outcome IN ('none', 'success', 'mistake', 'mixed')",
        name="ck_experiential_journal_lesson_outcome",
    ),
)

Index(
    "ix_experiential_journal_occurred_at_desc",
    experiential_journal_entries.c.occurred_at.desc(),
    experiential_journal_entries.c.id.desc(),
)

experiential_sentiment_snapshots = Table(
    "experiential_sentiment_snapshots",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("origin", String(8), nullable=False),
    Column("label", String(16), nullable=False),
    Column("product_id", String(32), nullable=True),
    Column("note", Text(), nullable=False, server_default=""),
    Column("journal_id", UUID(), nullable=True),
    CheckConstraint("origin IN ('human', 'agent')", name="ck_experiential_sentiment_origin"),
    CheckConstraint(
        "label IN ('bullish', 'bearish', 'neutral', 'unknown')",
        name="ck_experiential_sentiment_label",
    ),
)

Index(
    "ix_experiential_sentiment_occurred_at_desc",
    experiential_sentiment_snapshots.c.occurred_at.desc(),
    experiential_sentiment_snapshots.c.id.desc(),
)

experiential_pattern_observations = Table(
    "experiential_pattern_observations",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("origin", String(8), nullable=False),
    Column("pattern_key", String(63), nullable=False),
    Column("name", String(120), nullable=False),
    Column("hypothesis", Text(), nullable=False),
    Column("status", String(16), nullable=False),
    Column("evidence_kind", String(16), nullable=False),
    Column("evidence_id", String(128), nullable=True),
    Column("note", Text(), nullable=False, server_default=""),
    CheckConstraint("origin IN ('human', 'agent')", name="ck_experiential_pattern_origin"),
    CheckConstraint(
        "status IN ('hypothesized', 'supported', 'contradicted', 'retired')",
        name="ck_experiential_pattern_status",
    ),
    CheckConstraint(
        "evidence_kind IN ("
        "'none', 'backtest', 'paper_fill', 'live_fill', 'deployment', 'research', 'market_data'"
        ")",
        name="ck_experiential_pattern_evidence_kind",
    ),
)

Index(
    "ix_experiential_pattern_occurred_at_desc",
    experiential_pattern_observations.c.occurred_at.desc(),
    experiential_pattern_observations.c.id.desc(),
)

experiential_notifications = Table(
    "experiential_notifications",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("origin", String(8), nullable=False),
    Column("title", String(200), nullable=False),
    Column("body", Text(), nullable=False),
    Column("severity", String(16), nullable=False),
    Column("provider", String(16), nullable=False),
    Column("delivery_status", String(16), nullable=False),
    Column("detail", String(500), nullable=False, server_default=""),
    Column("journal_id", UUID(), nullable=True),
    CheckConstraint("origin IN ('human', 'agent')", name="ck_experiential_notification_origin"),
    CheckConstraint(
        "severity IN ('info', 'warning', 'error')",
        name="ck_experiential_notification_severity",
    ),
    CheckConstraint(
        "provider IN ('none', 'log', 'webhook')",
        name="ck_experiential_notification_provider",
    ),
    CheckConstraint(
        "delivery_status IN ('skipped', 'logged', 'delivered', 'failed')",
        name="ck_experiential_notification_delivery_status",
    ),
)

Index(
    "ix_experiential_notifications_occurred_at_desc",
    experiential_notifications.c.occurred_at.desc(),
    experiential_notifications.c.id.desc(),
)

experiential_models = Table(
    "experiential_models",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("origin", String(8), nullable=False),
    Column("engine_id", String(64), nullable=False),
    Column("seed", Integer(), nullable=False),
    Column("fingerprint", String(71), nullable=False),
    Column("canonical_document", Text(), nullable=False),
    CheckConstraint("origin IN ('human', 'agent')", name="ck_experiential_models_origin"),
    CheckConstraint(
        "engine_id = 'thytrader-experiential-train-v1'",
        name="ck_experiential_models_engine_id",
    ),
    CheckConstraint("seed >= 0", name="ck_experiential_models_seed"),
    CheckConstraint(
        "fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_experiential_models_fingerprint",
    ),
    UniqueConstraint("fingerprint", name="uq_experiential_models_fingerprint"),
)

Index(
    "ix_experiential_models_recorded_at_desc",
    experiential_models.c.recorded_at.desc(),
    experiential_models.c.id.desc(),
)

__all__ = [
    "experiential_journal_entries",
    "experiential_models",
    "experiential_notifications",
    "experiential_pattern_observations",
    "experiential_sentiment_snapshots",
]
