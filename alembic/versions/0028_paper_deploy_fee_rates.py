"""Persist paper deploy maker/taker fee assumptions on deployments.

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add paper fee columns, backfill documented defaults, and constrain live nulls."""
    op.add_column(
        "deployments",
        sa.Column("paper_maker_fee_rate", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "deployments",
        sa.Column("paper_taker_fee_rate", sa.String(length=64), nullable=True),
    )
    op.execute(
        """
        UPDATE deployments
        SET paper_maker_fee_rate = '0.001', paper_taker_fee_rate = '0.002'
        WHERE mode = 'paper'
        """
    )
    op.create_check_constraint(
        "ck_deployments_paper_fee_rates",
        "deployments",
        "(mode = 'paper' AND paper_maker_fee_rate IS NOT NULL "
        "AND paper_taker_fee_rate IS NOT NULL) OR "
        "(mode = 'live' AND paper_maker_fee_rate IS NULL AND paper_taker_fee_rate IS NULL)",
    )


def downgrade() -> None:
    """Drop paper fee columns and the paper/live fee-rate check."""
    op.drop_constraint("ck_deployments_paper_fee_rates", "deployments", type_="check")
    op.drop_column("deployments", "paper_taker_fee_rate")
    op.drop_column("deployments", "paper_maker_fee_rate")
