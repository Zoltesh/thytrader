"""Add live-extras trailing, OCO trigger, and user-order feed state.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_OLD_KIND = "kind IN ('post_only_limit', 'marketable')"
_NEW_KIND = "kind IN ('post_only_limit', 'marketable', 'trigger_bracket')"


def upgrade() -> None:
    """Add nullable trail/OCO columns and the singleton user-order feed table."""
    op.add_column(
        "execution_positions",
        sa.Column("trail_extreme", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "order_intents",
        sa.Column("stop_trigger_price", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "execution_orders",
        sa.Column("stop_trigger_price", sa.String(length=64), nullable=True),
    )
    op.drop_constraint("ck_order_intents_kind", "order_intents", type_="check")
    op.create_check_constraint("ck_order_intents_kind", "order_intents", _NEW_KIND)
    op.create_table(
        "user_order_feed_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_user_order_feed_state_singleton"),
        sa.CheckConstraint(
            "state IN ('disconnected', 'connecting', 'connected',"
            " 'stale', 'reconnecting', 'disabled')",
            name="ck_user_order_feed_state_value",
        ),
    )


def downgrade() -> None:
    """Remove live-extras columns and the user-order feed table."""
    op.drop_table("user_order_feed_state")
    op.drop_constraint("ck_order_intents_kind", "order_intents", type_="check")
    op.create_check_constraint("ck_order_intents_kind", "order_intents", _OLD_KIND)
    op.drop_column("execution_orders", "stop_trigger_price")
    op.drop_column("order_intents", "stop_trigger_price")
    op.drop_column("execution_positions", "trail_extreme")
