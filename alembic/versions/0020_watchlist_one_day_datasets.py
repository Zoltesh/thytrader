"""Allow 1d complete-only datasets on the market-data watchlist.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-14
"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_OLD = "timeframe IN ('1h', '5m', '15m', '30m', '6h')"
_NEW = "timeframe IN ('1h', '5m', '15m', '30m', '6h', '1d')"


def upgrade() -> None:
    """Permit 1d watchlist targets without rewriting existing 1h/5m/15m/30m/6h rows."""
    op.drop_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", type_="check")
    op.create_check_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", _NEW)


def downgrade() -> None:
    """Restore the 1h/5m/15m/30m/6h watchlist constraint. Rows with 1d must be removed first."""
    op.drop_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", type_="check")
    op.create_check_constraint("ck_market_data_watchlist_timeframe", "market_data_watchlist", _OLD)
