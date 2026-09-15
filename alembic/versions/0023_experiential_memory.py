"""Create experiential-memory tables and allow memory audit events.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

_OLD_AUDIT = (
    "category IN ("
    "'connection', 'snapshot', 'worker_error', 'market_data', 'websocket', "
    "'research', 'runtime'"
    ")"
)
_NEW_AUDIT = (
    "category IN ("
    "'connection', 'snapshot', 'worker_error', 'market_data', 'websocket', "
    "'research', 'runtime', 'memory'"
    ")"
)
_EVIDENCE = (
    "evidence_kind IN ("
    "'none', 'backtest', 'paper_fill', 'live_fill', 'deployment', 'research', 'market_data'"
    ")"
)


def upgrade() -> None:
    """Add experiential memory tables and expand the audit category check."""
    op.drop_constraint("ck_audit_events_category", "audit_events", type_="check")
    op.create_check_constraint("ck_audit_events_category", "audit_events", _NEW_AUDIT)
    _create_journals()
    _create_sentiment()
    _create_patterns()
    _create_notifications()


def downgrade() -> None:
    """Drop experiential memory tables and restore the prior audit category check."""
    op.drop_index("ix_experiential_notifications_occurred_at_desc", "experiential_notifications")
    op.drop_table("experiential_notifications")
    op.drop_index(
        "ix_experiential_pattern_occurred_at_desc",
        "experiential_pattern_observations",
    )
    op.drop_table("experiential_pattern_observations")
    op.drop_index(
        "ix_experiential_sentiment_occurred_at_desc",
        "experiential_sentiment_snapshots",
    )
    op.drop_table("experiential_sentiment_snapshots")
    op.drop_index("ix_experiential_journal_occurred_at_desc", "experiential_journal_entries")
    op.drop_table("experiential_journal_entries")
    op.drop_constraint("ck_audit_events_category", "audit_events", type_="check")
    op.create_check_constraint("ck_audit_events_category", "audit_events", _OLD_AUDIT)


def _create_journals() -> None:
    """Create append-only journal entries."""
    op.create_table(
        "experiential_journal_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin", sa.String(length=8), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("evidence_kind", sa.String(length=16), nullable=False),
        sa.Column("evidence_id", sa.String(length=128), nullable=True),
        sa.Column("product_id", sa.String(length=32), nullable=True),
        sa.Column("runtime_mode", sa.String(length=16), nullable=False),
        sa.Column("lesson_outcome", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("origin IN ('human', 'agent')", name="ck_experiential_journal_origin"),
        sa.CheckConstraint(
            "kind IN ('fact', 'lesson', 'note')", name="ck_experiential_journal_kind"
        ),
        sa.CheckConstraint(_EVIDENCE, name="ck_experiential_journal_evidence_kind"),
        sa.CheckConstraint(
            "runtime_mode IN ('none', 'research', 'paper', 'live')",
            name="ck_experiential_journal_runtime_mode",
        ),
        sa.CheckConstraint(
            "lesson_outcome IN ('none', 'success', 'mistake', 'mixed')",
            name="ck_experiential_journal_lesson_outcome",
        ),
    )
    op.create_index(
        "ix_experiential_journal_occurred_at_desc",
        "experiential_journal_entries",
        [sa.text("occurred_at DESC"), sa.text("id DESC")],
        unique=False,
    )


def _create_sentiment() -> None:
    """Create append-only sentiment snapshots."""
    op.create_table(
        "experiential_sentiment_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin", sa.String(length=8), nullable=False),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("product_id", sa.String(length=32), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("journal_id", sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("origin IN ('human', 'agent')", name="ck_experiential_sentiment_origin"),
        sa.CheckConstraint(
            "label IN ('bullish', 'bearish', 'neutral', 'unknown')",
            name="ck_experiential_sentiment_label",
        ),
    )
    op.create_index(
        "ix_experiential_sentiment_occurred_at_desc",
        "experiential_sentiment_snapshots",
        [sa.text("occurred_at DESC"), sa.text("id DESC")],
        unique=False,
    )


def _create_patterns() -> None:
    """Create append-only pattern-learning observations."""
    op.create_table(
        "experiential_pattern_observations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin", sa.String(length=8), nullable=False),
        sa.Column("pattern_key", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("evidence_kind", sa.String(length=16), nullable=False),
        sa.Column("evidence_id", sa.String(length=128), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("origin IN ('human', 'agent')", name="ck_experiential_pattern_origin"),
        sa.CheckConstraint(
            "status IN ('hypothesized', 'supported', 'contradicted', 'retired')",
            name="ck_experiential_pattern_status",
        ),
        sa.CheckConstraint(_EVIDENCE, name="ck_experiential_pattern_evidence_kind"),
    )
    op.create_index(
        "ix_experiential_pattern_occurred_at_desc",
        "experiential_pattern_observations",
        [sa.text("occurred_at DESC"), sa.text("id DESC")],
        unique=False,
    )


def _create_notifications() -> None:
    """Create append-only notification attempts."""
    op.create_table(
        "experiential_notifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin", sa.String(length=8), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("delivery_status", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("journal_id", sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "origin IN ('human', 'agent')",
            name="ck_experiential_notification_origin",
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error')",
            name="ck_experiential_notification_severity",
        ),
        sa.CheckConstraint(
            "provider IN ('none', 'log', 'webhook')",
            name="ck_experiential_notification_provider",
        ),
        sa.CheckConstraint(
            "delivery_status IN ('skipped', 'logged', 'delivered', 'failed')",
            name="ck_experiential_notification_delivery_status",
        ),
    )
    op.create_index(
        "ix_experiential_notifications_occurred_at_desc",
        "experiential_notifications",
        [sa.text("occurred_at DESC"), sa.text("id DESC")],
        unique=False,
    )
