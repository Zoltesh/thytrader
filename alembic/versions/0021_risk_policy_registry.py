"""Create immutable risk-policy publication tables.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add published risk-policy versions and a singleton active pointer."""
    op.create_table(
        "published_risk_policies",
        sa.Column("policy_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("policy_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("canonical_definition", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("policy_fingerprint"),
        sa.UniqueConstraint(
            "policy_id",
            "version",
            name="ux_published_risk_policy_identity_version",
        ),
        sa.CheckConstraint("version > 0", name="ck_published_risk_policy_version_positive"),
        sa.CheckConstraint(
            "policy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_published_risk_policy_fingerprint_format",
        ),
    )
    op.create_table(
        "active_risk_policy",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("policy_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_fingerprint"],
            ["published_risk_policies.policy_fingerprint"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_active_risk_policy_singleton"),
    )


def downgrade() -> None:
    """Drop the risk-policy registry tables."""
    op.drop_table("active_risk_policy")
    op.drop_table("published_risk_policies")
