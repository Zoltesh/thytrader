"""Protection lifecycle, leases, live capital, and durable loss baselines.

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add lifecycle, capital, latch, and active-identity uniqueness columns."""
    op.add_column(
        "deployments",
        sa.Column(
            "lifecycle_command",
            sa.String(length=32),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "deployments", sa.Column("allocated_capital", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "deployments", sa.Column("venue_available_quote", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "deployments", sa.Column("reserved_buying_power", sa.String(length=64), nullable=True)
    )
    op.add_column("deployments", sa.Column("inventory_cost", sa.String(length=64), nullable=True))
    op.add_column(
        "deployments", sa.Column("performance_equity", sa.String(length=64), nullable=True)
    )
    op.add_column("deployments", sa.Column("initial_equity", sa.String(length=64), nullable=True))
    op.add_column("deployments", sa.Column("baseline_equity", sa.String(length=64), nullable=True))
    op.add_column(
        "deployments", sa.Column("utc_day_open_equity", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "deployments",
        sa.Column("utc_day_open_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "deployments", sa.Column("high_water_mark_equity", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "deployments",
        sa.Column("daily_loss_latched", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "deployments",
        sa.Column("drawdown_latched", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "deployments",
        sa.Column("last_signal_event_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "deployments",
        sa.Column("last_signal_processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_deployments_lifecycle_command",
        "deployments",
        "lifecycle_command IN ('none', 'stop_new_entries', 'flatten', 'managed_shutdown')",
    )
    op.create_index(
        "ux_deployments_active_strategy_mode",
        "deployments",
        ["strategy_id", "mode"],
        unique=True,
        postgresql_where=sa.text("strategy_id IS NOT NULL AND status IN ('running', 'paused')"),
    )


def downgrade() -> None:
    """Drop lifecycle accounting columns and the active-identity index."""
    op.drop_index("ux_deployments_active_strategy_mode", table_name="deployments")
    op.drop_constraint("ck_deployments_lifecycle_command", "deployments", type_="check")
    op.drop_column("deployments", "last_signal_processed_at")
    op.drop_column("deployments", "last_signal_event_at")
    op.drop_column("deployments", "drawdown_latched")
    op.drop_column("deployments", "daily_loss_latched")
    op.drop_column("deployments", "high_water_mark_equity")
    op.drop_column("deployments", "utc_day_open_at")
    op.drop_column("deployments", "utc_day_open_equity")
    op.drop_column("deployments", "baseline_equity")
    op.drop_column("deployments", "initial_equity")
    op.drop_column("deployments", "performance_equity")
    op.drop_column("deployments", "inventory_cost")
    op.drop_column("deployments", "reserved_buying_power")
    op.drop_column("deployments", "venue_available_quote")
    op.drop_column("deployments", "allocated_capital")
    op.drop_column("deployments", "lifecycle_command")
