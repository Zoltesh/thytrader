"""Persist immutable research campaigns and forward validation state (ADR 0109)."""

import sqlalchemy as sa

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add an independent research table compatible with existing workers."""
    op.create_table(
        "research_campaigns",
        sa.Column("campaign_id", sa.UUID(), primary_key=True),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("manifest_fingerprint", sa.String(71), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index(
        "ix_research_campaigns_pending", "research_campaigns", ["completed", "updated_at"]
    )


def downgrade() -> None:
    """Remove only the campaign records, preserving all child research evidence."""
    op.drop_index("ix_research_campaigns_pending", table_name="research_campaigns")
    op.drop_table("research_campaigns")
