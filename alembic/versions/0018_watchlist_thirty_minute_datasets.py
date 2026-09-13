"""Allow 30m complete-only datasets on the market-data watchlist.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-13
"""

from __future__ import annotations

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_OLD = "timeframe IN ('1h', '5m', '15m')"
_NEW = "timeframe IN ('1h', '5m', '15m', '30m')"


def upgrade() -> None:
    """Permit 30m watchlist targets without rewriting existing 1h, 5m, or 15m rows."""
    op.drop_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", type_="check")
    op.create_check_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", _NEW)


def downgrade() -> None:
    """Restore the 1h/5m/15m watchlist constraint. Rows with 30m must be removed first."""
    op.drop_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", type_="check")
    op.create_check_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", _OLD)
