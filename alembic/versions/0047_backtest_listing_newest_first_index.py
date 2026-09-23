"""Newest-first index for unfiltered backtest result discovery.

Revision ID: 0047
Revises: 0046
Create Date: 2026-09-22
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Back the unfiltered (published_at DESC, result_fingerprint ASC) listing."""
    op.create_index(
        "ix_published_backtest_results_published_result",
        "published_backtest_results",
        [text("published_at DESC"), "result_fingerprint"],
    )


def downgrade() -> None:
    """Drop the unfiltered newest-first discovery index."""
    op.drop_index(
        "ix_published_backtest_results_published_result",
        table_name="published_backtest_results",
    )
