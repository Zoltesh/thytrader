"""Persist capital-normalized performance independently of fill-ledger baselines.

Revision ID: 0061
Revises: 0060
ADR 0107. Additive nullable columns preserve existing ledger and journal evidence.
"""

import sqlalchemy as sa

from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add performance metadata without backfilling or rewriting historical balances."""
    op.add_column(
        "deployments", sa.Column("performance_capital_quote", sa.String(64), nullable=True)
    )
    op.add_column(
        "deployments",
        sa.Column("performance_maximum_drawdown_fraction", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    """Refuse to discard pinned capital or observed drawdown evidence."""
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM deployments WHERE performance_capital_quote IS NOT NULL "
                "OR performance_maximum_drawdown_fraction IS NOT NULL"
            )
        )
        .scalar_one()
    )
    if count:
        raise RuntimeError("Cannot downgrade 0061 while performance metadata exists.")
    op.drop_column("deployments", "performance_maximum_drawdown_fraction")
    op.drop_column("deployments", "performance_capital_quote")
