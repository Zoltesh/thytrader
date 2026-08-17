"""Persist durable strategy drafts and immutable archive markers.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create lifecycle metadata without mutating immutable strategy documents."""
    op.add_column(
        "published_strategy_versions",
        sa.Column("source_draft_revision", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_published_strategy_source_draft_revision_positive",
        "published_strategy_versions",
        "source_draft_revision IS NULL OR source_draft_revision > 0",
    )
    op.create_table(
        "strategy_drafts",
        sa.Column("strategy_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("canonical_definition", sa.Text(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_strategy_draft_version_positive"),
        sa.CheckConstraint("revision > 0", name="ck_strategy_draft_revision_positive"),
        sa.PrimaryKeyConstraint("strategy_id", "version"),
    )
    op.create_table(
        "archived_strategy_versions",
        sa.Column("strategy_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "strategy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_archived_strategy_fingerprint_format",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_fingerprint"],
            ["published_strategy_versions.strategy_fingerprint"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("strategy_fingerprint"),
    )


def downgrade() -> None:
    """Remove mutable drafts and archive markers without touching immutable publications."""
    op.drop_table("archived_strategy_versions")
    op.drop_table("strategy_drafts")
    op.execute(
        "ALTER TABLE published_strategy_versions "
        "DROP CONSTRAINT IF EXISTS ck_published_strategy_source_draft_revision_positive"
    )
    op.execute(
        "ALTER TABLE published_strategy_versions DROP COLUMN IF EXISTS source_draft_revision"
    )
