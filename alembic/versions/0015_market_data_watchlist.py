"""Create the market-data ingestion watchlist.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Persist operator-managed product and timeframe ingestion targets."""
    op.create_table(
        "market_data_watchlist",
        sa.Column("provider", sa.String(length=32), primary_key=True),
        sa.Column("product_id", sa.String(length=32), primary_key=True),
        sa.Column("timeframe", sa.String(length=8), primary_key=True),
        sa.Column("lookback_hours", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "timeframe IN ('1h', '5m')",
            name="ck_market_data_watchlist_timeframe",
        ),
        sa.CheckConstraint(
            "lookback_hours >= 1 AND lookback_hours <= 2160",
            name="ck_market_data_watchlist_lookback_hours",
        ),
    )


def downgrade() -> None:
    """Drop the ingestion watchlist."""
    op.drop_table("market_data_watchlist")
