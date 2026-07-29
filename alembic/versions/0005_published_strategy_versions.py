"""Create immutable published strategies and exact dataset bindings.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create append-only strategy publication and dataset-association tables."""
    op.create_table(
        "published_strategy_versions",
        sa.Column("strategy_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("strategy_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("canonical_definition", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("strategy_fingerprint"),
        sa.UniqueConstraint(
            "strategy_id",
            "version",
            name="ux_published_strategy_identity_version",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_published_strategy_version_positive",
        ),
        sa.CheckConstraint(
            "strategy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_published_strategy_fingerprint_format",
        ),
    )
    op.create_table(
        "strategy_dataset_bindings",
        sa.Column("strategy_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("dataset_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_fingerprint"],
            ["published_strategy_versions.strategy_fingerprint"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("strategy_fingerprint", "dataset_fingerprint"),
        sa.CheckConstraint(
            "strategy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_strategy_dataset_binding_strategy_fingerprint_format",
        ),
        sa.CheckConstraint(
            "dataset_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_strategy_dataset_binding_dataset_fingerprint_format",
        ),
    )
    op.create_index(
        "ix_strategy_dataset_bindings_dataset_fingerprint",
        "strategy_dataset_bindings",
        ["dataset_fingerprint"],
        unique=False,
    )


def downgrade() -> None:
    """Remove strategy bindings before their referenced published definitions."""
    op.drop_index(
        "ix_strategy_dataset_bindings_dataset_fingerprint",
        table_name="strategy_dataset_bindings",
    )
    op.drop_table("strategy_dataset_bindings")
    op.drop_table("published_strategy_versions")
