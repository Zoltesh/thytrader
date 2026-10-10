"""Mirror the Coinbase CFM futures account read-only (ADR 0127).

Revision ID: 0072
Revises: 0071
Create Date: 2026-10-10

Additive: two new tables, no existing row or constraint changes.

* ``futures_account_snapshots``: one row per mirror cycle with the CFM balance summary
  (exact USD strings, ``NULL`` when unknown), the margin-window measures, the intraday
  margin setting, the current margin window and killswitch flags, enablement
  (``enabled``, ``not_enabled`` or ``unknown``) and the failed reads.
* ``futures_position_snapshots``: that cycle's open positions in contracts.

Downgrade drops both tables; they hold a mirror of venue state, not ThyTrader's own
records, so nothing is lost that the next read cannot observe again.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None

_AMOUNT_COLUMNS = (
    "futures_buying_power",
    "total_usd_balance",
    "cbi_usd_balance",
    "cfm_usd_balance",
    "total_open_orders_hold_amount",
    "unrealized_pnl",
    "daily_realized_pnl",
    "initial_margin",
    "available_margin",
    "liquidation_threshold",
    "liquidation_buffer_amount",
    "liquidation_buffer_percentage",
    "total_pending_transfers_amount",
    "funding_pnl",
)


def upgrade() -> None:
    """Create the account and position snapshot tables."""
    op.create_table(
        "futures_account_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enablement", sa.String(length=16), nullable=False),
        sa.Column("read_failures", sa.Text(), nullable=False, server_default="[]"),
        *(sa.Column(name, sa.String(length=64), nullable=True) for name in _AMOUNT_COLUMNS),
        sa.Column("intraday_margin_measure", sa.Text(), nullable=True),
        sa.Column("overnight_margin_measure", sa.Text(), nullable=True),
        sa.Column("intraday_margin_setting", sa.String(length=64), nullable=True),
        sa.Column("margin_window_type", sa.String(length=64), nullable=True),
        sa.Column("margin_window_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("intraday_killswitch_enabled", sa.Boolean(), nullable=True),
        sa.Column("enrollment_killswitch_enabled", sa.Boolean(), nullable=True),
        sa.Column("position_count", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "enablement IN ('enabled', 'not_enabled', 'unknown')",
            name="ck_futures_account_snapshots_enablement",
        ),
        sa.CheckConstraint(
            "position_count IS NULL OR position_count >= 0",
            name="ck_futures_account_snapshots_position_count",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_futures_account_snapshots_observed_at",
        "futures_account_snapshots",
        ["observed_at"],
        unique=False,
    )
    op.create_table(
        "futures_position_snapshots",
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("number_of_contracts", sa.String(length=64), nullable=False),
        sa.Column("current_price", sa.String(length=64), nullable=True),
        sa.Column("avg_entry_price", sa.String(length=64), nullable=True),
        sa.Column("unrealized_pnl", sa.String(length=64), nullable=True),
        sa.Column("daily_realized_pnl", sa.String(length=64), nullable=True),
        sa.Column("expiration_time", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["futures_account_snapshots.id"],
            ondelete="CASCADE",
            name="fk_futures_position_snapshots_snapshot_id",
        ),
        sa.CheckConstraint(
            "side IN ('long', 'short', 'unknown')",
            name="ck_futures_position_snapshots_side",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "product_id"),
    )


def downgrade() -> None:
    """Drop the mirror tables."""
    op.drop_table("futures_position_snapshots")
    op.drop_index(
        "ix_futures_account_snapshots_observed_at", table_name="futures_account_snapshots"
    )
    op.drop_table("futures_account_snapshots")
