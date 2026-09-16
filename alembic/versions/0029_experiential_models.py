"""Persist fingerprintable experiential models trained from attributed journals.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the append-only experiential-model table. Journal schema is unchanged."""
    op.create_table(
        "experiential_models",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin", sa.String(length=8), nullable=False),
        sa.Column("engine_id", sa.String(length=64), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=71), nullable=False),
        sa.Column("canonical_document", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_experiential_models_fingerprint"),
        sa.CheckConstraint(
            "origin IN ('human', 'agent')",
            name="ck_experiential_models_origin",
        ),
        sa.CheckConstraint(
            "engine_id = 'thytrader-experiential-train-v1'",
            name="ck_experiential_models_engine_id",
        ),
        sa.CheckConstraint("seed >= 0", name="ck_experiential_models_seed"),
        sa.CheckConstraint(
            "fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_experiential_models_fingerprint",
        ),
    )
    op.create_index(
        "ix_experiential_models_recorded_at_desc",
        "experiential_models",
        [sa.text("recorded_at DESC"), sa.text("id DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Drop trained experiential models. Journal tables stay."""
    op.drop_index("ix_experiential_models_recorded_at_desc", "experiential_models")
    op.drop_table("experiential_models")
