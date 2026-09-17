"""Raise watchlist lookback ceiling for slow venue clocks.

Revision ID: 0041
Revises: 0040
Create Date: 2026-09-17
"""

from __future__ import annotations

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow up to 8,760 hours (365 days) on 2h+ watch targets."""
    op.drop_constraint(
        "ck_market_data_watchlist_lookback_hours",
        "market_data_watchlist",
        type_="check",
    )
    op.create_check_constraint(
        "ck_market_data_watchlist_lookback_hours",
        "market_data_watchlist",
        "lookback_hours >= 1 AND lookback_hours <= 8760",
    )


def downgrade() -> None:
    """Restore the 90-day watchlist lookback ceiling."""
    op.drop_constraint(
        "ck_market_data_watchlist_lookback_hours",
        "market_data_watchlist",
        type_="check",
    )
    op.create_check_constraint(
        "ck_market_data_watchlist_lookback_hours",
        "market_data_watchlist",
        "lookback_hours >= 1 AND lookback_hours <= 2160",
    )
