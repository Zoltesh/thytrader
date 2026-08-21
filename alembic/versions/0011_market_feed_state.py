"""Create latest-state table for the public Coinbase market ticker.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the latest public ticker snapshot table."""
    op.create_table(
        "market_feed_state",
        sa.Column("product_id", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ticker_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_price", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("product_id"),
        sa.CheckConstraint(
            "state IN ('disconnected', 'connecting', 'connected',"
            " 'stale', 'reconnecting', 'disabled')",
            name="ck_market_feed_state_value",
        ),
    )


def downgrade() -> None:
    """Drop the public ticker snapshot table."""
    op.drop_table("market_feed_state")
