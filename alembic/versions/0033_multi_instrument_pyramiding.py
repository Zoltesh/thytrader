"""Persist per-product runtime overlays, position books, and order product ids.

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Key positions by product, stamp product_id on intents/orders, and store overlays."""
    op.add_column("order_intents", sa.Column("product_id", sa.String(length=32), nullable=True))
    op.add_column("execution_orders", sa.Column("product_id", sa.String(length=32), nullable=True))
    op.add_column(
        "execution_positions",
        sa.Column("product_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "execution_positions",
        sa.Column("add_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute(
        """
        UPDATE order_intents AS intents
        SET product_id = deployments.product_id
        FROM deployments
        WHERE intents.deployment_id = deployments.id
        """
    )
    op.execute(
        """
        UPDATE execution_orders AS orders
        SET product_id = deployments.product_id
        FROM deployments
        WHERE orders.deployment_id = deployments.id
        """
    )
    op.execute(
        """
        UPDATE execution_positions AS positions
        SET product_id = deployments.product_id
        FROM deployments
        WHERE positions.deployment_id = deployments.id
        """
    )
    op.alter_column("order_intents", "product_id", nullable=False)
    op.alter_column("execution_orders", "product_id", nullable=False)
    op.alter_column("execution_positions", "product_id", nullable=False)
    op.drop_constraint("execution_positions_pkey", "execution_positions", type_="primary")
    op.create_primary_key(
        "pk_execution_positions",
        "execution_positions",
        ["deployment_id", "product_id"],
    )
    op.create_check_constraint(
        "ck_execution_positions_add_count",
        "execution_positions",
        "add_count >= 1 AND add_count <= 8",
    )
    op.create_table(
        "execution_instrument_state",
        sa.Column("deployment_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.String(length=32), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("last_evaluated_bar", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_signal", sa.String(length=32), nullable=True),
        sa.Column("pending_entry_bars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bars_held", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cooldown_bars_remaining", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_stop_price", sa.String(length=64), nullable=True),
        sa.Column("pending_target_price", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("deployment_id", "product_id", name="pk_execution_instrument_state"),
        sa.CheckConstraint(
            "phase IN ('flat', 'pending_entry', 'open', 'pending_exit')",
            name="ck_execution_instrument_state_phase",
        ),
    )


def downgrade() -> None:
    """Drop overlays and restore a single position row per deployment."""
    op.drop_table("execution_instrument_state")
    op.drop_constraint("ck_execution_positions_add_count", "execution_positions", type_="check")
    op.drop_constraint("pk_execution_positions", "execution_positions", type_="primary")
    op.execute(
        """
        DELETE FROM execution_positions AS leftover
        WHERE leftover.ctid NOT IN (
            SELECT MIN(keep.ctid)
            FROM execution_positions AS keep
            GROUP BY keep.deployment_id
        )
        """
    )
    op.drop_column("execution_positions", "add_count")
    op.drop_column("execution_positions", "product_id")
    op.create_primary_key("execution_positions_pkey", "execution_positions", ["deployment_id"])
    op.drop_column("execution_orders", "product_id")
    op.drop_column("order_intents", "product_id")
