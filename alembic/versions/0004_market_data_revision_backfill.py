"""Backfill immutable revision numbers for legacy verified datasets.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Assign revision one to complete fingerprint-bearing rows created before 0003."""
    op.execute(
        sa.text(
            "UPDATE market_data_worker_state SET dataset_revision = 1 "
            "WHERE dataset_revision = 0 AND complete IS TRUE "
            "AND content_fingerprint IS NOT NULL"
        )
    )


def downgrade() -> None:
    """Leave corrected revision metadata intact because its origin is not reversible."""
