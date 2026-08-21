"""Create append-only operational audit events.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create append-only structured audit trail table."""
    op.create_table(
        "audit_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("product_id", sa.String(length=32), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "category IN ('connection', 'snapshot', 'worker_error', 'market_data', 'websocket')",
            name="ck_audit_events_category",
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure', 'info')",
            name="ck_audit_events_outcome",
        ),
    )
    op.create_index(
        "ix_audit_events_occurred_at_desc",
        "audit_events",
        [sa.text("occurred_at DESC"), sa.text("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_category_occurred_at_desc",
        "audit_events",
        ["category", sa.text("occurred_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Drop audit events table and its indexes."""
    op.drop_index(
        "ix_audit_events_category_occurred_at_desc",
        table_name="audit_events",
    )
    op.drop_index(
        "ix_audit_events_occurred_at_desc",
        table_name="audit_events",
    )
    op.drop_table("audit_events")
