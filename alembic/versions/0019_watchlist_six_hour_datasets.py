"""Allow 6h complete-only datasets on the market-data watchlist.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-14
"""

from __future__ import annotations

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_OLD = "timeframe IN ('1h', '5m', '15m', '30m')"
_NEW = "timeframe IN ('1h', '5m', '15m', '30m', '6h')"


def upgrade() -> None:
    """Permit 6h watchlist targets without rewriting existing 1h/5m/15m/30m rows."""
    op.drop_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", type_="check")
    op.create_check_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", _NEW)


def downgrade() -> None:
    """Restore the 1h/5m/15m/30m watchlist constraint. Rows with 6h must be removed first."""
    op.drop_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", type_="check")
    op.create_check_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", _OLD)
