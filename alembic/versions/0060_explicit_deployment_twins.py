"""Persist explicit paper/live pairs without guessing partners for existing bots.

Revision ID: 0060
Revises: 0059
ADR 0102. The separate relationship survives worker saves and lease changes.
"""

import sqlalchemy as sa

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add an empty one-to-one comparison relationship with durable references."""
    op.create_table(
        "deployment_twin_links",
        sa.Column("paper_deployment_id", sa.UUID(), primary_key=True),
        sa.Column("live_deployment_id", sa.UUID(), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["paper_deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["live_deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("live_deployment_id", name="ux_deployment_twin_links_live"),
        sa.CheckConstraint(
            "paper_deployment_id <> live_deployment_id", name="ck_deployment_twin_links_distinct"
        ),
    )


def downgrade() -> None:
    """Refuse to discard operator-selected pairs; allow downgrade of an empty table."""
    count = (
        op.get_bind().execute(sa.text("SELECT COUNT(*) FROM deployment_twin_links")).scalar_one()
    )
    if count:
        raise RuntimeError("Cannot downgrade 0060 while explicit deployment twin links exist.")
    op.drop_table("deployment_twin_links")
