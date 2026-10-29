"""Record the futures open-order listing in each CFM mirror snapshot (ADR 0151).

Revision ID: 0083
Revises: 0082
Create Date: 2026-10-29

``futures_account_snapshots`` gains ``open_futures_orders``: the same cycle's complete
nonterminal futures order listing as canonical JSON ``[[product_id, client_order_id], ...]``
(the client order id may be ``null``). ``NULL`` is unknown: the listing read failed, was not
attempted, or the row predates this revision. It is never an empty listing. Existing rows
are not backfilled: nothing observed those orders at the time.

The downgrade drops the column; past listings are lost, which only removes history (readers
then treat product exclusivity as unknown, exactly as before this revision).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None

_TABLE = "futures_account_snapshots"
_COLUMN = "open_futures_orders"


def upgrade() -> None:
    """Add the nullable open-order listing column."""
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the open-order listing column (history only)."""
    op.drop_column(_TABLE, _COLUMN)
