"""Add continuous market-data maintenance coordination facts.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Extend worker state for cumulative dataset maintenance and scheduling."""
    op.add_column(
        "market_data_worker_state",
        sa.Column("expected_ends_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "market_data_worker_state",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "market_data_worker_state",
        sa.Column("dataset_revision", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "market_data_worker_state",
        sa.Column(
            "maintenance_kind",
            sa.String(length=32),
            server_default="initial_backfill",
            nullable=False,
        ),
    )
    op.add_column(
        "market_data_worker_state",
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    """Remove continuous-maintenance coordination facts."""
    op.drop_column("market_data_worker_state", "enabled")
    op.drop_column("market_data_worker_state", "maintenance_kind")
    op.drop_column("market_data_worker_state", "dataset_revision")
    op.drop_column("market_data_worker_state", "next_retry_at")
    op.drop_column("market_data_worker_state", "expected_ends_at")
