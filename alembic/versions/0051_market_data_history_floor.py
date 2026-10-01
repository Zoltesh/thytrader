"""Record a confirmed provider-history floor on market-data worker state.

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-01

Prefix backfill used to retry the same incomplete provider day forever when Coinbase
genuinely lacks a bar directly before a complete island. Every cycle returned
``chunk_incomplete`` and re-recorded the unchanged island, so the island never extended
forward (2h/4h watches froze at 2026-09-17T04:00). The nullable ``history_floor_at``
column records that confirmed hole at the island start, so the worker stops prepending
there and resumes forward extension. Existing rows start with ``NULL`` and self-heal on
the next worker cycle. No candles are interpolated.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable provider-history floor to worker state."""
    op.add_column(
        "market_data_worker_state",
        sa.Column(
            "history_floor_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "Confirmed provider hole directly before the island; prefix backfill stops here."
            ),
        ),
    )


def downgrade() -> None:
    """Remove the provider-history floor."""
    op.drop_column("market_data_worker_state", "history_floor_at")
