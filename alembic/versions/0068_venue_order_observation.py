"""Keep successful venue order-state observations separate from local write timestamps.

Revision ID: 0068
Revises: 0067
"""

import sqlalchemy as sa

from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable evidence; never manufacture verification time for historical rows."""
    op.add_column(
        "execution_orders",
        sa.Column("venue_observed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Remove only observation metadata, preserving order identities and economics."""
    op.drop_column("execution_orders", "venue_observed_at")
