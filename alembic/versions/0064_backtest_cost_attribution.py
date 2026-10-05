"""Persist bounded fee attribution without changing canonical result bytes.

Revision ID: 0064
Revises: 0063
Old workers remain compatible with the nullable publication column (ADR 0109).
"""

import sqlalchemy as sa

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add optional closed-trade attribution alongside immutable result evidence."""
    op.add_column(
        "published_backtest_results", sa.Column("cost_attribution_json", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    """Remove regenerable attribution while preserving canonical results."""
    op.drop_column("published_backtest_results", "cost_attribution_json")
