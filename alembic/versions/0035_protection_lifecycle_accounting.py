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


def _column_names(table: str) -> set[str]:
    """Return column names currently present on one table."""
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    """True when ``name`` is already an index on ``table``."""
    inspector = sa.inspect(op.get_bind())
    return any(index["name"] == name for index in inspector.get_indexes(table))


def _has_check(table: str, name: str) -> bool:
    """True when ``name`` is already a check constraint on ``table``."""
    inspector = sa.inspect(op.get_bind())
    return any(item["name"] == name for item in inspector.get_check_constraints(table))


def _has_fk(table: str, name: str) -> bool:
    """True when ``name`` is already a foreign key on ``table``."""
    inspector = sa.inspect(op.get_bind())
    return any(item.get("name") == name for item in inspector.get_foreign_keys(table))


def _ensure_column(table: str, column: sa.Column) -> None:
    """Add ``column`` only when the table does not already have it."""
    if column.name not in _column_names(table):
        op.add_column(table, column)


def _ensure_atomic_ledger_columns() -> None:
    """Apply ADR 0057 columns when a 0033→0035 stamp skipped revision 0034."""
    _ensure_column(
        "execution_fills",
        sa.Column("economics_applied_at", sa.DateTime(timezone=True), nullable=True),
    )
    _ensure_column(
        "deployments",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    )
    _ensure_column(
        "deployments",
        sa.Column("worker_lease_holder", sa.String(length=128), nullable=True),
    )
    _ensure_column(
        "deployments",
        sa.Column("worker_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    _ensure_column("execution_orders", sa.Column("parent_order_id", sa.UUID(), nullable=True))
    _ensure_column(
        "execution_orders",
        sa.Column("attached_child_venue_order_id", sa.String(length=128), nullable=True),
    )
    _ensure_column(
        "execution_orders",
        sa.Column("pyramid_add", sa.Boolean(), nullable=False, server_default="false"),
    )
    if not _has_fk("execution_orders", "fk_execution_orders_parent_order_id"):
        op.create_foreign_key(
            "fk_execution_orders_parent_order_id",
            "execution_orders",
            "execution_orders",
            ["parent_order_id"],
            ["id"],
            ondelete="SET NULL",
        )


def upgrade() -> None:
    """Add lifecycle, capital, latch, and active-identity uniqueness columns."""
    _ensure_atomic_ledger_columns()
    _ensure_column(
        "deployments",
        sa.Column(
            "lifecycle_command",
            sa.String(length=32),
            nullable=False,
            server_default="none",
        ),
    )
    _ensure_column(
        "deployments", sa.Column("allocated_capital", sa.String(length=64), nullable=True)
    )
    _ensure_column(
        "deployments", sa.Column("venue_available_quote", sa.String(length=64), nullable=True)
    )
    _ensure_column(
        "deployments", sa.Column("reserved_buying_power", sa.String(length=64), nullable=True)
    )
    _ensure_column("deployments", sa.Column("inventory_cost", sa.String(length=64), nullable=True))
    _ensure_column(
        "deployments", sa.Column("performance_equity", sa.String(length=64), nullable=True)
    )
    _ensure_column("deployments", sa.Column("initial_equity", sa.String(length=64), nullable=True))
    _ensure_column("deployments", sa.Column("baseline_equity", sa.String(length=64), nullable=True))
    _ensure_column(
        "deployments", sa.Column("utc_day_open_equity", sa.String(length=64), nullable=True)
    )
    _ensure_column(
        "deployments",
        sa.Column("utc_day_open_at", sa.DateTime(timezone=True), nullable=True),
    )
    _ensure_column(
        "deployments", sa.Column("high_water_mark_equity", sa.String(length=64), nullable=True)
    )
    _ensure_column(
        "deployments",
        sa.Column("daily_loss_latched", sa.Boolean(), nullable=False, server_default="false"),
    )
    _ensure_column(
        "deployments",
        sa.Column("drawdown_latched", sa.Boolean(), nullable=False, server_default="false"),
    )
    _ensure_column(
        "deployments",
        sa.Column("last_signal_event_at", sa.DateTime(timezone=True), nullable=True),
    )
    _ensure_column(
        "deployments",
        sa.Column("last_signal_processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    if not _has_check("deployments", "ck_deployments_lifecycle_command"):
        op.create_check_constraint(
            "ck_deployments_lifecycle_command",
            "deployments",
            "lifecycle_command IN ('none', 'stop_new_entries', 'flatten', 'managed_shutdown')",
        )
    if not _has_index("deployments", "ux_deployments_active_strategy_mode"):
        op.create_index(
            "ux_deployments_active_strategy_mode",
            "deployments",
            ["strategy_id", "mode"],
            unique=True,
            postgresql_where=sa.text("strategy_id IS NOT NULL AND status IN ('running', 'paused')"),
        )


def downgrade() -> None:
    """Drop lifecycle accounting columns and the active-identity index."""
    if _has_index("deployments", "ux_deployments_active_strategy_mode"):
        op.drop_index("ux_deployments_active_strategy_mode", table_name="deployments")
    if _has_check("deployments", "ck_deployments_lifecycle_command"):
        op.drop_constraint("ck_deployments_lifecycle_command", "deployments", type_="check")
    for name in (
        "last_signal_processed_at",
        "last_signal_event_at",
        "drawdown_latched",
        "daily_loss_latched",
        "high_water_mark_equity",
        "utc_day_open_at",
        "utc_day_open_equity",
        "baseline_equity",
        "initial_equity",
        "performance_equity",
        "inventory_cost",
        "reserved_buying_power",
        "venue_available_quote",
        "allocated_capital",
        "lifecycle_command",
    ):
        if name in _column_names("deployments"):
            op.drop_column("deployments", name)
