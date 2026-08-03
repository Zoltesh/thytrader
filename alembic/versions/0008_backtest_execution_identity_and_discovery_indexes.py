"""Add semantic backtest-run identity and deterministic result discovery indexes.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add one nullable semantic identity and indexed read-only discovery access paths."""
    op.add_column(
        "published_research_run_specs",
        sa.Column("execution_fingerprint", sa.String(length=71), nullable=True),
    )
    op.create_index(
        "ux_published_research_run_specs_execution_fingerprint",
        "published_research_run_specs",
        ["execution_fingerprint"],
        unique=True,
        postgresql_where=sa.text("execution_fingerprint IS NOT NULL"),
    )
    op.create_index(
        "ix_published_backtest_results_run_published",
        "published_backtest_results",
        ["run_fingerprint", sa.text("published_at DESC"), "result_fingerprint"],
    )
    op.create_index(
        "ix_published_backtest_results_strategy_published",
        "published_backtest_results",
        ["strategy_fingerprint", sa.text("published_at DESC"), "result_fingerprint"],
    )


def downgrade() -> None:
    """Remove the additive identity and discovery indexes."""
    op.drop_index(
        "ix_published_backtest_results_strategy_published",
        table_name="published_backtest_results",
    )
    op.drop_index(
        "ix_published_backtest_results_run_published",
        table_name="published_backtest_results",
    )
    op.drop_index(
        "ux_published_research_run_specs_execution_fingerprint",
        table_name="published_research_run_specs",
    )
    op.drop_column("published_research_run_specs", "execution_fingerprint")
