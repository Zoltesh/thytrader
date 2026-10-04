"""Persist bounded derived metrics without changing historical result bytes.

Revision ID: 0062
Revises: 0061
ADR 0109. Old workers remain compatible with the nullable column.
"""

import sqlalchemy as sa

from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add a nullable derived-metrics publication alongside immutable result bytes."""
    op.add_column("published_backtest_results", sa.Column("metrics_json", sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove regenerable derived metrics while preserving original result evidence."""
    op.drop_column("published_backtest_results", "metrics_json")
