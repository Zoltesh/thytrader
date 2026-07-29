"""Create durable market-data worker state.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the latest-state table for independently supervised ingestion."""
    op.create_table(
        "market_data_worker_state",
        sa.Column("provider", sa.String(length=32), primary_key=True),
        sa.Column("product_id", sa.String(length=32), primary_key=True),
        sa.Column("timeframe", sa.String(length=8), primary_key=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("covered_starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("covered_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_candle_count", sa.Integer(), nullable=True),
        sa.Column("received_candle_count", sa.Integer(), nullable=True),
        sa.Column("gap_count", sa.Integer(), nullable=True),
        sa.Column("missing_intervals", sa.Integer(), nullable=True),
        sa.Column("complete", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=71), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.String(length=256), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    """Drop durable market-data worker state."""
    op.drop_table("market_data_worker_state")
