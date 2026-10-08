"""Market-data worker coverage state and watchlist tables."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    String,
    Table,
)

from thytrader.persistence.schema_metadata import metadata

market_data_worker_state = Table(
    "market_data_worker_state",
    metadata,
    Column("provider", String(32), primary_key=True),
    Column("product_id", String(32), primary_key=True),
    Column("timeframe", String(8), primary_key=True),
    Column("status", String(16), nullable=False),
    Column("last_attempt_at", DateTime(timezone=True), nullable=False),
    Column("last_success_at", DateTime(timezone=True), nullable=True),
    Column("requested_starts_at", DateTime(timezone=True), nullable=False),
    Column("requested_ends_at", DateTime(timezone=True), nullable=False),
    Column("covered_starts_at", DateTime(timezone=True), nullable=True),
    Column("covered_ends_at", DateTime(timezone=True), nullable=True),
    Column("expected_candle_count", Integer(), nullable=True),
    Column("received_candle_count", Integer(), nullable=True),
    Column("gap_count", Integer(), nullable=True),
    Column("missing_intervals", Integer(), nullable=True),
    Column("complete", Boolean(), nullable=False, server_default="false"),
    Column("content_fingerprint", String(71), nullable=True),
    Column("failure_code", String(64), nullable=True),
    Column("failure_message", String(256), nullable=True),
    Column("consecutive_failures", Integer(), nullable=False, server_default="0"),
    Column("expected_ends_at", DateTime(timezone=True), nullable=True),
    Column("next_retry_at", DateTime(timezone=True), nullable=True),
    Column("dataset_revision", Integer(), nullable=False, server_default="0"),
    Column("maintenance_kind", String(32), nullable=False, server_default="initial_backfill"),
    Column("enabled", Boolean(), nullable=False, server_default="true"),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column(
        "history_floor_at",
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "Listing floor: no provider candle before the island start back past the "
            "timeframe's lookback ceiling (ADR 0095); prefix backfill stops here."
        ),
    ),
)

market_data_watchlist = Table(
    "market_data_watchlist",
    metadata,
    Column("provider", String(32), primary_key=True),
    Column("product_id", String(32), primary_key=True),
    Column("timeframe", String(8), primary_key=True),
    Column("lookback_hours", Integer(), nullable=False),
    Column("enabled", Boolean(), nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("ingest_requested_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "timeframe IN ('1h', '5m', '15m', '30m', '6h', '1d', '1m', '2h', '4h')",
        name="ck_market_data_watchlist_timeframe",
    ),
    # Widest per-timeframe ceiling (ten years); Alembic 0052 and ADR 0085.
    CheckConstraint(
        "lookback_hours >= 1 AND lookback_hours <= 87600",
        name="ck_market_data_watchlist_lookback_hours",
    ),
)

__all__ = [
    "market_data_watchlist",
    "market_data_worker_state",
]
