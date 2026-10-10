"""CFM futures account mirror tables (ADR 0127, Alembic 0072).

Every amount is an exact USD decimal string; ``NULL`` is unknown, never zero. Column notes
are Python comments: the creating migration sets no database comments.
"""

from __future__ import annotations

from sqlalchemy import (
    UUID,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    Text,
)

from thytrader.persistence.schema_metadata import metadata

_AMOUNT = String(64)

futures_account_snapshots = Table(
    "futures_account_snapshots",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("provider", String(32), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("enablement", String(16), nullable=False),
    # Failed reads as JSON ["operation:reason", ...]; no venue text.
    Column("read_failures", Text(), nullable=False, server_default="[]"),
    Column("futures_buying_power", _AMOUNT, nullable=True),
    Column("total_usd_balance", _AMOUNT, nullable=True),
    Column("cbi_usd_balance", _AMOUNT, nullable=True),
    Column("cfm_usd_balance", _AMOUNT, nullable=True),
    Column("total_open_orders_hold_amount", _AMOUNT, nullable=True),
    Column("unrealized_pnl", _AMOUNT, nullable=True),
    Column("daily_realized_pnl", _AMOUNT, nullable=True),
    Column("initial_margin", _AMOUNT, nullable=True),
    Column("available_margin", _AMOUNT, nullable=True),
    Column("liquidation_threshold", _AMOUNT, nullable=True),
    Column("liquidation_buffer_amount", _AMOUNT, nullable=True),
    Column("liquidation_buffer_percentage", _AMOUNT, nullable=True),
    Column("total_pending_transfers_amount", _AMOUNT, nullable=True),
    Column("funding_pnl", _AMOUNT, nullable=True),
    # Intraday and overnight margin-window measures as canonical JSON of exact strings.
    Column("intraday_margin_measure", Text(), nullable=True),
    Column("overnight_margin_measure", Text(), nullable=True),
    Column("intraday_margin_setting", String(64), nullable=True),
    Column("margin_window_type", String(64), nullable=True),
    Column("margin_window_end_at", DateTime(timezone=True), nullable=True),
    Column("intraday_killswitch_enabled", Boolean(), nullable=True),
    Column("enrollment_killswitch_enabled", Boolean(), nullable=True),
    # NULL when the position read failed (unknown); otherwise the number of child rows.
    Column("position_count", Integer(), nullable=True),
    CheckConstraint(
        "enablement IN ('enabled', 'not_enabled', 'unknown')",
        name="ck_futures_account_snapshots_enablement",
    ),
    CheckConstraint(
        "position_count IS NULL OR position_count >= 0",
        name="ck_futures_account_snapshots_position_count",
    ),
    Index("ix_futures_account_snapshots_observed_at", "observed_at"),
)

futures_position_snapshots = Table(
    "futures_position_snapshots",
    metadata,
    Column("snapshot_id", UUID(), primary_key=True),
    Column("product_id", String(32), primary_key=True),
    Column("side", String(8), nullable=False),
    Column("number_of_contracts", _AMOUNT, nullable=False),  # Contracts, not base units.
    Column("current_price", _AMOUNT, nullable=True),
    Column("avg_entry_price", _AMOUNT, nullable=True),
    Column("unrealized_pnl", _AMOUNT, nullable=True),
    Column("daily_realized_pnl", _AMOUNT, nullable=True),
    Column("expiration_time", DateTime(timezone=True), nullable=True),
    ForeignKeyConstraint(
        ["snapshot_id"],
        ["futures_account_snapshots.id"],
        ondelete="CASCADE",
        name="fk_futures_position_snapshots_snapshot_id",
    ),
    CheckConstraint(
        "side IN ('long', 'short', 'unknown')",
        name="ck_futures_position_snapshots_side",
    ),
)

__all__ = [
    "futures_account_snapshots",
    "futures_position_snapshots",
]
