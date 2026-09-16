"""Add position side and attached take-profit prices for spot shorts and entry brackets.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Persist attached take-profit on intents/orders and long/short position side."""
    op.add_column(
        "order_intents",
        sa.Column("take_profit_price", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "execution_orders",
        sa.Column("take_profit_price", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "execution_positions",
        sa.Column("side", sa.String(length=8), nullable=False, server_default="long"),
    )
    op.create_check_constraint(
        "ck_execution_positions_side",
        "execution_positions",
        "side IN ('long', 'short')",
    )


def downgrade() -> None:
    """Drop attached take-profit columns and position side."""
    op.drop_constraint("ck_execution_positions_side", "execution_positions", type_="check")
    op.drop_column("execution_positions", "side")
    op.drop_column("execution_orders", "take_profit_price")
    op.drop_column("order_intents", "take_profit_price")
