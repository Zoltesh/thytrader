"""Create immutable published bar-level backtest results.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create append-only canonical result records bound to exact published run identities."""
    op.create_table(
        "published_backtest_results",
        sa.Column("result_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("run_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("strategy_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("dataset_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("signal_trace_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("canonical_result", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("result_fingerprint"),
        sa.ForeignKeyConstraint(
            ["run_fingerprint"],
            ["published_research_run_specs.run_fingerprint"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "result_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_backtest_result_fingerprint_format",
        ),
        sa.CheckConstraint(
            "run_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_backtest_result_run_fingerprint_format",
        ),
        sa.CheckConstraint(
            "strategy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_backtest_result_strategy_fingerprint_format",
        ),
        sa.CheckConstraint(
            "dataset_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_backtest_result_dataset_fingerprint_format",
        ),
        sa.CheckConstraint(
            "signal_trace_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_backtest_result_signal_trace_fingerprint_format",
        ),
    )
    op.create_index(
        "ix_published_backtest_results_dataset_fingerprint",
        "published_backtest_results",
        ["dataset_fingerprint"],
        unique=False,
    )


def downgrade() -> None:
    """Remove derived result records without touching immutable source publications."""
    op.drop_index(
        "ix_published_backtest_results_dataset_fingerprint",
        table_name="published_backtest_results",
    )
    op.drop_table("published_backtest_results")
