"""Persist composed research-study catalog rows.

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the append-only research-study catalog."""
    op.create_table(
        "published_research_studies",
        sa.Column("study_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("engine_contract_version", sa.String(length=64), nullable=False),
        sa.Column("product_id", sa.String(length=32), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("window_count", sa.Integer(), nullable=False),
        sa.Column("selected_strategy_fingerprint", sa.String(length=71), nullable=True),
        sa.Column("mean_oos_return_fraction", sa.String(length=64), nullable=True),
        sa.Column("stitched_oos_available", sa.Boolean(), nullable=True),
        sa.Column("selection_metric", sa.String(length=64), nullable=True),
        sa.Column("canonical_study", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("study_fingerprint"),
        sa.CheckConstraint(
            "study_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_research_study_fingerprint_format",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_research_study_request_fingerprint_format",
        ),
        sa.CheckConstraint(
            "kind IN ("
            "'oos_holdout', 'walk_forward', 'cross_market', "
            "'parameter_sweep', 'walk_forward_optimization'"
            ")",
            name="ck_research_study_kind",
        ),
        sa.CheckConstraint(
            "timeframe IN ('1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d')",
            name="ck_research_study_timeframe",
        ),
        sa.CheckConstraint("window_count >= 1", name="ck_research_study_window_count"),
        sa.CheckConstraint(
            "selected_strategy_fingerprint IS NULL OR "
            "selected_strategy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_research_study_selected_fingerprint_format",
        ),
    )
    op.create_index(
        "ix_published_research_studies_published_at_desc",
        "published_research_studies",
        ["published_at", "study_fingerprint"],
        unique=False,
    )
    op.create_index(
        "ix_published_research_studies_kind_published",
        "published_research_studies",
        ["kind", "published_at", "study_fingerprint"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the research-study catalog."""
    op.drop_index(
        "ix_published_research_studies_kind_published",
        table_name="published_research_studies",
    )
    op.drop_index(
        "ix_published_research_studies_published_at_desc",
        table_name="published_research_studies",
    )
    op.drop_table("published_research_studies")
