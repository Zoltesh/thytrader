"""Record spot USDC and USD balances in each CFM mirror snapshot (ADR 0127 §10).

Revision ID: 0074
Revises: 0073
Create Date: 2026-10-10

``futures_account_snapshots`` gains four nullable exact-decimal columns read in the same
mirror cycle from the spot account listing: ``spot_usdc_available``, ``spot_usdc_hold``,
``spot_usd_available`` and ``spot_usd_hold``. ``NULL`` is unknown (the spot read failed, or
the row predates this revision), never zero. Existing rows are not backfilled: nothing
observed those balances at the time.

The downgrade drops the columns; the spot balances of past cycles are lost, which only
removes history (no trading state depends on them).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None

_TABLE = "futures_account_snapshots"
_COLUMNS = ("spot_usdc_available", "spot_usdc_hold", "spot_usd_available", "spot_usd_hold")


def upgrade() -> None:
    """Add the four nullable spot balance columns."""
    for name in _COLUMNS:
        op.add_column(_TABLE, sa.Column(name, sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Drop the spot balance columns (history only)."""
    for name in reversed(_COLUMNS):
        op.drop_column(_TABLE, name)
