"""Allow 1m, 2h, and 4h complete-only datasets on the market-data watchlist.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-15
"""

from __future__ import annotations

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

_OLD = "timeframe IN ('1h', '5m', '15m', '30m', '6h', '1d')"
_NEW = "timeframe IN ('1h', '5m', '15m', '30m', '6h', '1d', '1m', '2h', '4h')"


def upgrade() -> None:
    """Permit 1m/2h/4h watchlist targets without rewriting existing rows."""
    op.drop_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", type_="check")
    op.create_check_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", _NEW)


def downgrade() -> None:
    """Restore the prior watchlist constraint. Rows with 1m, 2h, or 4h must be removed first."""
    op.drop_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", type_="check")
    op.create_check_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", _OLD)
