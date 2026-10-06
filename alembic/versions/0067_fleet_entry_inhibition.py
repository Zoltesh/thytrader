"""Durable fleet entry inhibition and idempotent fleet operations.

Revision ID: 0067
Revises: 0066

Seed the two mode latches without changing existing deployment status or policy.
The integrated release head is 0068.
"""

import sqlalchemy as sa

from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the latch, operation log, and stable inventory index."""
    op.create_table(
        "fleet_entry_inhibition",
        sa.Column("mode", sa.String(length=16), primary_key=True),
        sa.Column("inhibited", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.CheckConstraint("mode IN ('paper', 'live')", name="ck_fleet_entry_inhibition_mode"),
    )
    op.execute(
        sa.text(
            "INSERT INTO fleet_entry_inhibition (mode, inhibited, revision, updated_at) "
            "VALUES ('paper', false, 0, CURRENT_TIMESTAMP), "
            "('live', false, 0, CURRENT_TIMESTAMP)"
        )
    )
    op.create_table(
        "fleet_control_operations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=4000), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('disarm', 'managed_stop', 'flatten', 'rearm')",
            name="ck_fleet_control_operations_action",
        ),
        sa.CheckConstraint(
            "mode IN ('paper', 'live', 'all')", name="ck_fleet_control_operations_mode"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'partial', 'completed', 'rejected')",
            name="ck_fleet_control_operations_status",
        ),
        sa.UniqueConstraint("idempotency_key", name="ux_fleet_control_operations_idempotency_key"),
    )
    op.create_index(
        "ix_deployments_inventory_created_id",
        "deployments",
        [sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Remove fleet control tables and the inventory index."""
    op.drop_index("ix_deployments_inventory_created_id", table_name="deployments")
    op.drop_table("fleet_control_operations")
    op.drop_table("fleet_entry_inhibition")
