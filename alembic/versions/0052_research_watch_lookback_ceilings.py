"""Widen the watchlist lookback ceiling to research spans (ADR 0085, ops contract v45).

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-02

Per-timeframe ceilings live in ``thytrader.market_data.lookback`` (1m 90 days, 5m one
year, 15m two years, 30m three years, 1h five years, 2h/4h/6h/1d ten years). The
database keeps one widest bound, 87600 hours, so no stored row can exceed any ceiling.
Existing rows already satisfy it. Downgrade clamps longer rows back to the ADR 0068
ceilings (2160 hours for 1m-1h, 8760 hours for 2h-1d) before restoring 8760, so older
code never reads a row it would reject.
"""

from __future__ import annotations

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_market_data_watchlist_lookback_hours"
_TABLE = "market_data_watchlist"


def upgrade() -> None:
    """Allow lookbacks up to 87600 hours (ten years)."""
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "lookback_hours >= 1 AND lookback_hours <= 87600",
    )


def downgrade() -> None:
    """Clamp rows to the ADR 0068 ceilings, then restore the 8760-hour bound."""
    op.execute(
        "UPDATE market_data_watchlist SET lookback_hours = 2160 "
        "WHERE timeframe IN ('1m', '5m', '15m', '30m', '1h') AND lookback_hours > 2160"
    )
    op.execute(
        "UPDATE market_data_watchlist SET lookback_hours = 8760 "
        "WHERE timeframe IN ('2h', '4h', '6h', '1d') AND lookback_hours > 8760"
    )
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "lookback_hours >= 1 AND lookback_hours <= 8760",
    )
