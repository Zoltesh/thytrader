"""Queue worker-owned ingest and persist cross-process worker heartbeats.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ingest request timestamps and worker heartbeat rows."""
    op.add_column(
        "market_data_watchlist",
        sa.Column("ingest_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_name", sa.String(length=32), primary_key=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "worker_name IN ('portfolio_worker', 'market_data_worker', 'execution_worker')",
            name="ck_worker_heartbeats_name",
        ),
    )


def downgrade() -> None:
    """Drop heartbeat rows and ingest request timestamps."""
    op.drop_table("worker_heartbeats")
    op.drop_column("market_data_watchlist", "ingest_requested_at")
