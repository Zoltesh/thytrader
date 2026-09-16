"""Atomic fill economics, deployment revision, worker lease, and child order links.

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add fill-application markers, optimistic revision, leases, and child-order columns."""
    op.add_column(
        "execution_fills",
        sa.Column("economics_applied_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "deployments",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "deployments",
        sa.Column("worker_lease_holder", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "deployments",
        sa.Column("worker_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "execution_orders",
        sa.Column("parent_order_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "execution_orders",
        sa.Column("attached_child_venue_order_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "execution_orders",
        sa.Column("pyramid_add", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_foreign_key(
        "fk_execution_orders_parent_order_id",
        "execution_orders",
        "execution_orders",
        ["parent_order_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Drop ledger-hardening columns."""
    op.drop_constraint(
        "fk_execution_orders_parent_order_id",
        "execution_orders",
        type_="foreignkey",
    )
    op.drop_column("execution_orders", "pyramid_add")
    op.drop_column("execution_orders", "attached_child_venue_order_id")
    op.drop_column("execution_orders", "parent_order_id")
    op.drop_column("deployments", "worker_lease_expires_at")
    op.drop_column("deployments", "worker_lease_holder")
    op.drop_column("deployments", "revision")
    op.drop_column("execution_fills", "economics_applied_at")
