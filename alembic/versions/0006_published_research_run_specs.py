"""Create immutable published research-run specifications.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the append-only canonical research-run specification table."""
    op.create_table(
        "published_research_run_specs",
        sa.Column("run_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("strategy_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("dataset_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("canonical_specification", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("run_fingerprint"),
        sa.UniqueConstraint("run_id"),
        sa.ForeignKeyConstraint(
            ["strategy_fingerprint", "dataset_fingerprint"],
            [
                "strategy_dataset_bindings.strategy_fingerprint",
                "strategy_dataset_bindings.dataset_fingerprint",
            ],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "run_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_research_run_fingerprint_format",
        ),
        sa.CheckConstraint(
            "strategy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_research_run_strategy_fingerprint_format",
        ),
        sa.CheckConstraint(
            "dataset_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_research_run_dataset_fingerprint_format",
        ),
    )
    op.create_index(
        "ix_published_research_run_specs_dataset_fingerprint",
        "published_research_run_specs",
        ["dataset_fingerprint"],
        unique=False,
    )


def downgrade() -> None:
    """Remove immutable research-run specifications without altering prior artifacts."""
    op.drop_index(
        "ix_published_research_run_specs_dataset_fingerprint",
        table_name="published_research_run_specs",
    )
    op.drop_table("published_research_run_specs")
