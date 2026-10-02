"""Clear provider-history floors recorded before ADR 0095 so affected series backfill again.

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-02

ADR 0095. A one-time data repair plus a column comment; no structural change:

* Before ADR 0095 the market-data worker recorded ``history_floor_at`` at the first
  confirmed missing bar before an island. Coinbase omits candles for intervals without
  trades, so sparse markets got floors from interior no-trade gaps and from forward
  walks, and every quiet interval moved the floor forward (BONK-USD 1m kept two candles
  of a 90-day watch). A floor also blocks prefix backfill. The table never recorded which
  walk wrote a floor, so this migration clears every one. Each affected series then
  backfills again; a genuine listing floor is re-proven by the worker's listing search on
  its next visit, at a cost of a few provider requests per series.
* The column comment now states the ADR 0095 meaning.

Downgrade restores the old comment only. Cleared floors cannot be restored, and older
workers re-derive their own.
"""

from __future__ import annotations

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None

_NEW_COMMENT = (
    "Listing floor: no provider candle before the island start back past the timeframe's "
    "lookback ceiling (ADR 0095); prefix backfill stops here."
)
_OLD_COMMENT = "Confirmed provider hole directly before the island; prefix backfill stops here."


def upgrade() -> None:
    """Clear every recorded provider-history floor and restate the column's meaning."""
    op.execute(
        "UPDATE market_data_worker_state SET history_floor_at = NULL "
        "WHERE history_floor_at IS NOT NULL"
    )
    op.alter_column(
        "market_data_worker_state",
        "history_floor_at",
        comment=_NEW_COMMENT,
        existing_comment=_OLD_COMMENT,
    )


def downgrade() -> None:
    """Restore the previous column comment; cleared floors stay cleared."""
    op.alter_column(
        "market_data_worker_state",
        "history_floor_at",
        comment=_OLD_COMMENT,
        existing_comment=_NEW_COMMENT,
    )
