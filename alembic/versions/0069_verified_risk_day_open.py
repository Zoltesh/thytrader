"""Separate qualified risk day-opening evidence from legacy observed-equity stamps.

Revision ID: 0069
Revises: 0068
No backfill: existing UTC day-open fields do not establish midnight provenance.
"""

import sqlalchemy as sa

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable derived evidence without altering any recorded financial values."""
    op.add_column("deployments", sa.Column("risk_day_open_evidence", sa.Text(), nullable=True))


def downgrade() -> None:
    """Refuse to erase qualified opening evidence during a rollback."""
    count = (
        op.get_bind()
        .execute(
            sa.text("SELECT COUNT(*) FROM deployments WHERE risk_day_open_evidence IS NOT NULL")
        )
        .scalar_one()
    )
    if count:
        raise RuntimeError("Cannot downgrade 0069 while qualified risk opening evidence exists.")
    op.drop_column("deployments", "risk_day_open_evidence")
