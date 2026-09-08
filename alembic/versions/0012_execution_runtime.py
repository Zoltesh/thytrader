"""Create durable paper/live execution tables.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create deployment, intent, order, fill, and position tables."""
    op.create_table(
        "deployments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("strategy_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("strategy_id", sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("paper_starting_cash", sa.String(length=64), nullable=True),
        sa.Column("cash", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("last_evaluated_bar", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_signal", sa.String(length=32), nullable=True),
        sa.Column("mismatch_detail", sa.Text(), nullable=True),
        sa.Column("pending_entry_bars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bars_held", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cooldown_bars_remaining", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_stop_price", sa.String(length=64), nullable=True),
        sa.Column("pending_target_price", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_fingerprint"],
            ["published_strategy_versions.strategy_fingerprint"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("mode IN ('paper', 'live')", name="ck_deployments_mode"),
        sa.CheckConstraint(
            "status IN ('running', 'paused', 'stopped')",
            name="ck_deployments_status",
        ),
        sa.CheckConstraint(
            "phase IN ('flat', 'pending_entry', 'open', 'pending_exit')",
            name="ck_deployments_phase",
        ),
        sa.CheckConstraint(
            "strategy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_deployments_strategy_fingerprint_format",
        ),
    )
    op.create_index(
        "ix_deployments_strategy_updated",
        "deployments",
        ["strategy_id", sa.text("updated_at DESC")],
    )
    op.create_index(
        "ix_deployments_status_updated",
        "deployments",
        ["status", sa.text("updated_at DESC")],
    )
    op.create_table(
        "order_intents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("deployment_id", sa.UUID(), nullable=False),
        sa.Column("client_order_id", sa.String(length=128), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("price", sa.String(length=64), nullable=True),
        sa.Column("quantity", sa.String(length=64), nullable=False),
        sa.Column("candle_starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_order_id", name="ux_order_intents_client_order_id"),
        sa.CheckConstraint("side IN ('buy', 'sell')", name="ck_order_intents_side"),
        sa.CheckConstraint(
            "kind IN ('post_only_limit', 'marketable')",
            name="ck_order_intents_kind",
        ),
    )
    op.create_table(
        "execution_orders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("deployment_id", sa.UUID(), nullable=False),
        sa.Column("intent_id", sa.UUID(), nullable=False),
        sa.Column("client_order_id", sa.String(length=128), nullable=False),
        sa.Column("venue_order_id", sa.String(length=128), nullable=True),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("price", sa.String(length=64), nullable=True),
        sa.Column("quantity", sa.String(length=64), nullable=False),
        sa.Column("filled_quantity", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["intent_id"], ["order_intents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_order_id", name="ux_execution_orders_client_order_id"),
        sa.CheckConstraint("side IN ('buy', 'sell')", name="ck_execution_orders_side"),
        sa.CheckConstraint(
            "status IN ('pending', 'open', 'filled', 'canceled', 'rejected', 'unknown')",
            name="ck_execution_orders_status",
        ),
    )
    op.create_index(
        "ix_execution_orders_deployment_status",
        "execution_orders",
        ["deployment_id", "status"],
    )
    op.create_table(
        "execution_fills",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("deployment_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("venue_fill_id", sa.String(length=128), nullable=False),
        sa.Column("price", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.String(length=64), nullable=False),
        sa.Column("fee", sa.String(length=64), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["execution_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deployment_id", "venue_fill_id", name="ux_execution_fills_venue"),
    )
    op.create_table(
        "execution_positions",
        sa.Column("deployment_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.String(length=64), nullable=False),
        sa.Column("entry_price", sa.String(length=64), nullable=False),
        sa.Column("stop_price", sa.String(length=64), nullable=False),
        sa.Column("target_price", sa.String(length=64), nullable=False),
        sa.Column("entered_bar", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("deployment_id"),
    )


def downgrade() -> None:
    """Drop execution tables in reverse dependency order."""
    op.drop_table("execution_positions")
    op.drop_table("execution_fills")
    op.drop_index("ix_execution_orders_deployment_status", table_name="execution_orders")
    op.drop_table("execution_orders")
    op.drop_table("order_intents")
    op.drop_index("ix_deployments_status_updated", table_name="deployments")
    op.drop_index("ix_deployments_strategy_updated", table_name="deployments")
    op.drop_table("deployments")
