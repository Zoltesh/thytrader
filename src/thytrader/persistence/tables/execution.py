"""Execution feed cursors, deployments, and order lifecycle tables."""

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
    UniqueConstraint,
)

from thytrader.persistence.schema_metadata import metadata

market_feed_state = Table(
    "market_feed_state",
    metadata,
    Column("product_id", String(32), primary_key=True),
    Column("state", String(16), nullable=False),
    Column("last_message_at", DateTime(timezone=True), nullable=True),
    Column("last_ticker_at", DateTime(timezone=True), nullable=True),
    Column("last_price", String(64), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "state IN ('disconnected', 'connecting', 'connected', 'stale', 'reconnecting', 'disabled')",
        name="ck_market_feed_state_value",
    ),
)

user_order_feed_state = Table(
    "user_order_feed_state",
    metadata,
    Column("id", Integer(), primary_key=True),
    Column("state", String(16), nullable=False),
    Column("last_message_at", DateTime(timezone=True), nullable=True),
    Column("last_heartbeat_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("id = 1", name="ck_user_order_feed_state_singleton"),
    CheckConstraint(
        "state IN ('disconnected', 'connecting', 'connected', 'stale', 'reconnecting', 'disabled')",
        name="ck_user_order_feed_state_value",
    ),
)

deployments = Table(
    "deployments",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("strategy_fingerprint", String(71), nullable=True),
    Column("strategy_id", String(36), nullable=True),
    Column(
        "strategy_name",
        String(120),
        nullable=True,
        comment="Strategy name captured at start; survives strategy deletion.",
    ),
    Column(
        "portfolio_id",
        String(36),
        nullable=True,
        comment="Deployed portfolio this book is a sleeve of (ADR 0091); set at creation.",
    ),
    Column("product_id", String(32), nullable=False),
    Column("mode", String(8), nullable=False),
    Column("status", String(16), nullable=False),
    Column("kind", String(16), nullable=False, server_default="strategy"),
    Column("timeframe", String(8), nullable=True),
    Column("paper_starting_cash", String(64), nullable=True),
    Column("paper_maker_fee_rate", String(64), nullable=True),
    Column("paper_taker_fee_rate", String(64), nullable=True),
    Column("cash", String(64), nullable=False),
    Column("phase", String(32), nullable=False),
    Column("last_evaluated_bar", DateTime(timezone=True), nullable=True),
    Column("last_signal", String(32), nullable=True),
    Column("mismatch_detail", Text(), nullable=True),
    Column("pending_entry_bars", Integer(), nullable=False, server_default="0"),
    Column("bars_held", Integer(), nullable=False, server_default="0"),
    Column("cooldown_bars_remaining", Integer(), nullable=False, server_default="0"),
    Column("pending_stop_price", String(64), nullable=True),
    Column("pending_target_price", String(64), nullable=True),
    Column("revision", Integer(), nullable=False, server_default="0"),
    Column("worker_lease_holder", String(128), nullable=True),
    Column("worker_lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("lifecycle_command", String(32), nullable=False, server_default="none"),
    Column("allocated_capital", String(64), nullable=True),
    Column("venue_available_quote", String(64), nullable=True),
    Column("reserved_buying_power", String(64), nullable=True),
    Column("inventory_cost", String(64), nullable=True),
    Column("performance_equity", String(64), nullable=True),
    Column("performance_capital_quote", String(64), nullable=True),
    Column("performance_maximum_drawdown_fraction", String(64), nullable=True),
    Column("initial_equity", String(64), nullable=True),
    Column("baseline_equity", String(64), nullable=True),
    Column("utc_day_open_equity", String(64), nullable=True),
    Column("utc_day_open_at", DateTime(timezone=True), nullable=True),
    Column("risk_day_open_evidence", Text, nullable=True),
    Column("high_water_mark_equity", String(64), nullable=True),
    Column("daily_loss_latched", Boolean(), nullable=False, server_default="false"),
    Column("drawdown_latched", Boolean(), nullable=False, server_default="false"),
    Column("last_signal_event_at", DateTime(timezone=True), nullable=True),
    Column("last_signal_processed_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["strategy_fingerprint"],
        ["strategy_snapshots.strategy_fingerprint"],
        ondelete="RESTRICT",
        name="fk_deployments_strategy_snapshot",
    ),
    ForeignKeyConstraint(
        ["strategy_id"],
        ["strategies.strategy_id"],
        ondelete="SET NULL",
        name="fk_deployments_strategy_id",
    ),
    ForeignKeyConstraint(
        ["portfolio_id"],
        ["portfolios.portfolio_id"],
        ondelete="SET NULL",
        name="fk_deployments_portfolio_id",
    ),
    CheckConstraint("mode IN ('paper', 'live')", name="ck_deployments_mode"),
    CheckConstraint("status IN ('running', 'paused', 'stopped')", name="ck_deployments_status"),
    CheckConstraint(
        "phase IN ('flat', 'pending_entry', 'open', 'pending_exit')",
        name="ck_deployments_phase",
    ),
    CheckConstraint("kind IN ('strategy', 'discretionary')", name="ck_deployments_kind"),
    CheckConstraint(
        "timeframe IS NULL OR timeframe IN "
        "('1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d')",
        name="ck_deployments_timeframe",
    ),
    CheckConstraint(
        "strategy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_deployments_strategy_fingerprint_format",
    ),
    CheckConstraint(
        "("
        "kind = 'strategy' AND strategy_fingerprint IS NOT NULL AND ("
        "strategy_id IS NOT NULL OR status = 'stopped')"
        ") OR ("
        "kind = 'discretionary' AND strategy_fingerprint IS NULL AND strategy_id IS NULL "
        "AND timeframe IN ('1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d')"
        ")",
        name="ck_deployments_kind_identity",
    ),
    CheckConstraint(
        "("
        "mode = 'paper' AND paper_maker_fee_rate IS NOT NULL "
        "AND paper_taker_fee_rate IS NOT NULL"
        ") OR ("
        "mode = 'live' AND paper_maker_fee_rate IS NULL AND paper_taker_fee_rate IS NULL"
        ")",
        name="ck_deployments_paper_fee_rates",
    ),
    CheckConstraint(
        "lifecycle_command IN ('none', 'stop_new_entries', 'flatten', 'managed_shutdown')",
        name="ck_deployments_lifecycle_command",
    ),
)

Index("ix_deployments_strategy_updated", deployments.c.strategy_id, deployments.c.updated_at.desc())

Index("ix_deployments_status_updated", deployments.c.status, deployments.c.updated_at.desc())

Index(
    "ix_deployments_portfolio_id",
    deployments.c.portfolio_id,
    postgresql_where=deployments.c.portfolio_id.is_not(None),
)

Index(
    "ux_deployments_active_strategy_mode",
    deployments.c.strategy_id,
    deployments.c.mode,
    unique=True,
    postgresql_where=deployments.c.strategy_id.is_not(None)
    & deployments.c.status.in_(("running", "paused")),
)

deployment_twin_links = Table(
    "deployment_twin_links",
    metadata,
    Column("paper_deployment_id", UUID(), primary_key=True),
    Column("live_deployment_id", UUID(), nullable=False),
    Column("linked_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["paper_deployment_id"], ["deployments.id"], ondelete="CASCADE"),
    ForeignKeyConstraint(["live_deployment_id"], ["deployments.id"], ondelete="CASCADE"),
    UniqueConstraint("live_deployment_id", name="ux_deployment_twin_links_live"),
    CheckConstraint(
        "paper_deployment_id <> live_deployment_id", name="ck_deployment_twin_links_distinct"
    ),
)

order_intents = Table(
    "order_intents",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("deployment_id", UUID(), nullable=False),
    Column("client_order_id", String(128), nullable=False),
    Column("purpose", String(32), nullable=False),
    Column("side", String(8), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("price", String(64), nullable=True),
    Column("stop_trigger_price", String(64), nullable=True),
    Column("take_profit_price", String(64), nullable=True),
    Column("quantity", String(64), nullable=False),
    Column("candle_starts_at", DateTime(timezone=True), nullable=False),
    Column("status", String(16), nullable=False),
    Column("origin", String(8), nullable=False, server_default="runtime"),
    Column("idempotency_key", String(128), nullable=True),
    Column("product_id", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="RESTRICT"),
    UniqueConstraint("client_order_id", name="ux_order_intents_client_order_id"),
    UniqueConstraint("idempotency_key", name="ux_order_intents_idempotency_key"),
    CheckConstraint("side IN ('buy', 'sell')", name="ck_order_intents_side"),
    CheckConstraint(
        "kind IN ('post_only_limit', 'marketable', 'trigger_bracket', 'stop_limit')",
        name="ck_order_intents_kind",
    ),
    CheckConstraint(
        "origin IN ('human', 'agent', 'runtime')",
        name="ck_order_intents_origin",
    ),
)

execution_orders = Table(
    "execution_orders",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("deployment_id", UUID(), nullable=False),
    Column("intent_id", UUID(), nullable=False),
    Column("client_order_id", String(128), nullable=False),
    Column("venue_order_id", String(128), nullable=True),
    Column("side", String(8), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("price", String(64), nullable=True),
    Column("stop_trigger_price", String(64), nullable=True),
    Column("take_profit_price", String(64), nullable=True),
    Column("quantity", String(64), nullable=False),
    Column("filled_quantity", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("reject_reason", Text(), nullable=True),
    Column("product_id", String(32), nullable=False),
    Column("parent_order_id", UUID(), nullable=True),
    Column("attached_child_venue_order_id", String(128), nullable=True),
    Column("venue_observed_at", DateTime(timezone=True), nullable=True),
    Column("pyramid_add", Boolean(), nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="RESTRICT"),
    ForeignKeyConstraint(["intent_id"], ["order_intents.id"], ondelete="RESTRICT"),
    ForeignKeyConstraint(
        ["parent_order_id"],
        ["execution_orders.id"],
        ondelete="SET NULL",
        name="fk_execution_orders_parent_order_id",
    ),
    UniqueConstraint("client_order_id", name="ux_execution_orders_client_order_id"),
    CheckConstraint("side IN ('buy', 'sell')", name="ck_execution_orders_side"),
    CheckConstraint(
        "status IN ('pending', 'open', 'filled', 'canceled', 'rejected', 'unknown')",
        name="ck_execution_orders_status",
    ),
)

Index(
    "ix_execution_orders_deployment_status",
    execution_orders.c.deployment_id,
    execution_orders.c.status,
)

execution_fills = Table(
    "execution_fills",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("deployment_id", UUID(), nullable=False),
    Column("order_id", UUID(), nullable=False),
    Column("venue_fill_id", String(128), nullable=False),
    Column("price", String(64), nullable=False),
    Column("quantity", String(64), nullable=False),
    Column("fee", String(64), nullable=False),
    Column("filled_at", DateTime(timezone=True), nullable=False),
    Column("economics_applied_at", DateTime(timezone=True), nullable=True),
    ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="RESTRICT"),
    ForeignKeyConstraint(["order_id"], ["execution_orders.id"], ondelete="RESTRICT"),
    UniqueConstraint("deployment_id", "venue_fill_id", name="ux_execution_fills_venue"),
)

execution_positions = Table(
    "execution_positions",
    metadata,
    Column("deployment_id", UUID(), primary_key=True),
    Column("product_id", String(32), primary_key=True),
    Column("quantity", String(64), nullable=False),
    Column("entry_price", String(64), nullable=False),
    Column("stop_price", String(64), nullable=False),
    # Take-profit price; NULL when the strategy declares no take-profit (ADR 0090).
    # (Kept as a Python comment: the migration set no column comment.)
    Column("target_price", String(64), nullable=True),
    Column("entered_bar", DateTime(timezone=True), nullable=False),
    Column("trail_extreme", String(64), nullable=True),
    Column("side", String(8), nullable=False, server_default="long"),
    Column("add_count", Integer(), nullable=False, server_default="1"),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column(
        "signal_exit_bar",
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "UTC start of the closed bar whose exits.signal_exit rule matched; the book keeps "
            "exiting until flat (ADR 0093)."
        ),
    ),
    ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="RESTRICT"),
    CheckConstraint("side IN ('long', 'short')", name="ck_execution_positions_side"),
    CheckConstraint(
        "add_count >= 1 AND add_count <= 8",
        name="ck_execution_positions_add_count",
    ),
)

execution_instrument_state = Table(
    "execution_instrument_state",
    metadata,
    Column("deployment_id", UUID(), primary_key=True),
    Column("product_id", String(32), primary_key=True),
    Column("phase", String(32), nullable=False),
    Column("last_evaluated_bar", DateTime(timezone=True), nullable=True),
    Column("last_signal", String(32), nullable=True),
    Column("pending_entry_bars", Integer(), nullable=False, server_default="0"),
    Column("bars_held", Integer(), nullable=False, server_default="0"),
    Column("cooldown_bars_remaining", Integer(), nullable=False, server_default="0"),
    Column("pending_stop_price", String(64), nullable=True),
    Column("pending_target_price", String(64), nullable=True),
    ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="RESTRICT"),
    CheckConstraint(
        "phase IN ('flat', 'pending_entry', 'open', 'pending_exit')",
        name="ck_execution_instrument_state_phase",
    ),
)

Index(
    "ix_deployments_inventory_created_id",
    deployments.c.created_at.desc(),
    deployments.c.id.desc(),
)

__all__ = [
    "deployment_twin_links",
    "deployments",
    "execution_fills",
    "execution_instrument_state",
    "execution_orders",
    "execution_positions",
    "market_feed_state",
    "order_intents",
    "user_order_feed_state",
]
