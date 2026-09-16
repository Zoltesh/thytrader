"""SQLAlchemy Core metadata for append-only operational records."""

from __future__ import annotations

from sqlalchemy import (
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

portfolio_snapshots = Table(
    "portfolio_snapshots",
    metadata,
    Column(
        "id",
        BigInteger(),
        primary_key=True,
        autoincrement=True,
        comment="Monotonic surrogate key for append-only snapshots.",
    ),
    Column("as_of", DateTime(timezone=True), nullable=False, comment="Exchange snapshot instant."),
    Column("provider", String(32), nullable=False, comment="Exchange provider identifier."),
    Column(
        "connection_status",
        String(16),
        nullable=False,
        comment="Connection status at snapshot time.",
    ),
    Column("demo", Boolean(), nullable=False, comment="Whether the snapshot used demo data."),
    Column(
        "total_usd_value",
        Numeric(38, 18),
        nullable=False,
        comment="Exact total USD valuation as a decimal.",
    ),
    Column(
        "snapshot",
        nullable=False,
        comment="Complete JSON snapshot preserving all decimal strings.",
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
        comment="Database row insertion timestamp.",
    ),
)

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
    CheckConstraint(
        "lookback_hours >= 1 AND lookback_hours <= 2160",
        name="ck_market_data_watchlist_lookback_hours",
    ),
)

worker_heartbeats = Table(
    "worker_heartbeats",
    metadata,
    Column("worker_name", String(32), primary_key=True),
    Column("heartbeat_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "worker_name IN ('portfolio_worker', 'market_data_worker', 'execution_worker')",
        name="ck_worker_heartbeats_name",
    ),
)

strategy_drafts = Table(
    "strategy_drafts",
    metadata,
    Column("strategy_id", String(36), primary_key=True),
    Column("version", Integer(), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("canonical_definition", Text, nullable=False),
    CheckConstraint("version > 0", name="ck_strategy_draft_version_positive"),
    CheckConstraint("revision > 0", name="ck_strategy_draft_revision_positive"),
)


published_strategy_versions = Table(
    "published_strategy_versions",
    metadata,
    Column("strategy_fingerprint", String(71), primary_key=True),
    Column("strategy_id", String(36), nullable=False),
    Column("version", Integer(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("canonical_definition", Text(), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("source_draft_revision", BigInteger, nullable=True),
    UniqueConstraint(
        "strategy_id",
        "version",
        name="ux_published_strategy_identity_version",
    ),
    CheckConstraint(
        "version > 0",
        name="ck_published_strategy_version_positive",
    ),
    CheckConstraint(
        "strategy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_published_strategy_fingerprint_format",
    ),
    CheckConstraint(
        "source_draft_revision IS NULL OR source_draft_revision > 0",
        name="ck_published_strategy_source_draft_revision_positive",
    ),
)

archived_strategy_versions = Table(
    "archived_strategy_versions",
    metadata,
    Column("strategy_fingerprint", String(71), primary_key=True),
    Column("archived_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["strategy_fingerprint"],
        ["published_strategy_versions.strategy_fingerprint"],
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "strategy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_archived_strategy_fingerprint_format",
    ),
)


strategy_dataset_bindings = Table(
    "strategy_dataset_bindings",
    metadata,
    Column("strategy_fingerprint", String(71), primary_key=True),
    Column("dataset_fingerprint", String(71), primary_key=True),
    Column("bound_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["strategy_fingerprint"],
        ["published_strategy_versions.strategy_fingerprint"],
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "strategy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_strategy_dataset_binding_strategy_fingerprint_format",
    ),
    CheckConstraint(
        "dataset_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_strategy_dataset_binding_dataset_fingerprint_format",
    ),
)

published_research_run_specs = Table(
    "published_research_run_specs",
    metadata,
    Column("run_fingerprint", String(71), primary_key=True),
    Column("run_id", String(36), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("strategy_fingerprint", String(71), nullable=False),
    Column("dataset_fingerprint", String(71), nullable=False),
    Column("execution_fingerprint", String(71), nullable=True),
    Column("canonical_specification", Text(), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["strategy_fingerprint", "dataset_fingerprint"],
        [
            "strategy_dataset_bindings.strategy_fingerprint",
            "strategy_dataset_bindings.dataset_fingerprint",
        ],
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "run_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_research_run_fingerprint_format",
    ),
    CheckConstraint(
        "strategy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_research_run_strategy_fingerprint_format",
    ),
    CheckConstraint(
        "dataset_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_research_run_dataset_fingerprint_format",
    ),
)

Index(
    "ix_portfolio_snapshots_as_of_desc",
    portfolio_snapshots.c.as_of.desc(),
    portfolio_snapshots.c.id.desc(),
)

Index(
    "ix_strategy_dataset_bindings_dataset_fingerprint",
    strategy_dataset_bindings.c.dataset_fingerprint,
)

published_backtest_results = Table(
    "published_backtest_results",
    metadata,
    Column("result_fingerprint", String(71), primary_key=True),
    Column("run_fingerprint", String(71), nullable=False),
    Column("strategy_fingerprint", String(71), nullable=False),
    Column("dataset_fingerprint", String(71), nullable=False),
    Column("signal_trace_fingerprint", String(71), nullable=False),
    Column("canonical_result", Text(), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["run_fingerprint"],
        ["published_research_run_specs.run_fingerprint"],
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "result_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_backtest_result_fingerprint_format",
    ),
    CheckConstraint(
        "run_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_backtest_result_run_fingerprint_format",
    ),
    CheckConstraint(
        "strategy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_backtest_result_strategy_fingerprint_format",
    ),
    CheckConstraint(
        "dataset_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_backtest_result_dataset_fingerprint_format",
    ),
    CheckConstraint(
        "signal_trace_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_backtest_result_signal_trace_fingerprint_format",
    ),
)

Index(
    "ix_published_research_run_specs_dataset_fingerprint",
    published_research_run_specs.c.dataset_fingerprint,
)

Index(
    "ux_published_research_run_specs_execution_fingerprint",
    published_research_run_specs.c.execution_fingerprint,
    unique=True,
    postgresql_where=published_research_run_specs.c.execution_fingerprint.is_not(None),
)

Index(
    "ix_published_backtest_results_run_published",
    published_backtest_results.c.run_fingerprint,
    published_backtest_results.c.published_at.desc(),
    published_backtest_results.c.result_fingerprint.asc(),
)

Index(
    "ix_published_backtest_results_strategy_published",
    published_backtest_results.c.strategy_fingerprint,
    published_backtest_results.c.published_at.desc(),
    published_backtest_results.c.result_fingerprint.asc(),
)

Index(
    "ix_published_backtest_results_dataset_fingerprint",
    published_backtest_results.c.dataset_fingerprint,
)

published_research_studies = Table(
    "published_research_studies",
    metadata,
    Column("study_fingerprint", String(71), primary_key=True),
    Column("request_fingerprint", String(71), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("engine_contract_version", String(64), nullable=False),
    Column("product_id", String(32), nullable=False),
    Column("timeframe", String(8), nullable=False),
    Column("window_count", Integer(), nullable=False),
    Column("selected_strategy_fingerprint", String(71), nullable=True),
    Column("mean_oos_return_fraction", String(64), nullable=True),
    Column("stitched_oos_available", Boolean(), nullable=True),
    Column("selection_metric", String(64), nullable=True),
    Column("canonical_study", Text(), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "study_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_research_study_fingerprint_format",
    ),
    CheckConstraint(
        "request_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_research_study_request_fingerprint_format",
    ),
    CheckConstraint(
        "kind IN ("
        "'oos_holdout', 'walk_forward', 'cross_market', "
        "'parameter_sweep', 'walk_forward_optimization'"
        ")",
        name="ck_research_study_kind",
    ),
    CheckConstraint(
        "timeframe IN ('1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d')",
        name="ck_research_study_timeframe",
    ),
    CheckConstraint("window_count >= 1", name="ck_research_study_window_count"),
    CheckConstraint(
        "selected_strategy_fingerprint IS NULL OR "
        "selected_strategy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_research_study_selected_fingerprint_format",
    ),
)

Index(
    "ix_published_research_studies_published_at_desc",
    published_research_studies.c.published_at.desc(),
    published_research_studies.c.study_fingerprint.asc(),
)

Index(
    "ix_published_research_studies_kind_published",
    published_research_studies.c.kind,
    published_research_studies.c.published_at.desc(),
    published_research_studies.c.study_fingerprint.asc(),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", UUID(), primary_key=True, comment="Surrogate UUID identifier."),
    Column(
        "occurred_at",
        DateTime(timezone=True),
        nullable=False,
        comment="Event occurrence instant.",
    ),
    Column("category", String(32), nullable=False, comment="Functional event category."),
    Column("action", String(64), nullable=False, comment="Action or transition name."),
    Column("outcome", String(16), nullable=False, comment="Success, failure, or info."),
    Column("provider", String(32), nullable=True, comment="Optional exchange/service provider."),
    Column("product_id", String(32), nullable=True, comment="Optional product symbol."),
    Column(
        "detail",
        Text(),
        nullable=False,
        server_default="",
        comment="Redacted descriptive details.",
    ),
    Column(
        "recorded_at",
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
        comment="Row insertion instant.",
    ),
    CheckConstraint(
        "category IN ("
        "'connection', 'snapshot', 'worker_error', 'market_data', 'websocket', "
        "'research', 'runtime', 'memory'"
        ")",
        name="ck_audit_events_category",
    ),
    CheckConstraint(
        "outcome IN ('success', 'failure', 'info')",
        name="ck_audit_events_outcome",
    ),
)

Index(
    "ix_audit_events_occurred_at_desc",
    audit_events.c.occurred_at.desc(),
    audit_events.c.id.desc(),
)

Index(
    "ix_audit_events_category_occurred_at_desc",
    audit_events.c.category,
    audit_events.c.occurred_at.desc(),
)

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
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["strategy_fingerprint"],
        ["published_strategy_versions.strategy_fingerprint"],
        ondelete="RESTRICT",
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
        "kind = 'strategy' AND strategy_fingerprint IS NOT NULL AND strategy_id IS NOT NULL"
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
)

Index("ix_deployments_strategy_updated", deployments.c.strategy_id, deployments.c.updated_at.desc())
Index("ix_deployments_status_updated", deployments.c.status, deployments.c.updated_at.desc())

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
        "kind IN ('post_only_limit', 'marketable', 'trigger_bracket')",
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
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="RESTRICT"),
    ForeignKeyConstraint(["intent_id"], ["order_intents.id"], ondelete="RESTRICT"),
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
    Column("applied_at", DateTime(timezone=True), nullable=True),
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
    Column("target_price", String(64), nullable=False),
    Column("entered_bar", DateTime(timezone=True), nullable=False),
    Column("trail_extreme", String(64), nullable=True),
    Column("side", String(8), nullable=False, server_default="long"),
    Column("add_count", Integer(), nullable=False, server_default="1"),
    Column("last_fill_intent_id", UUID(), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="RESTRICT"),
    ForeignKeyConstraint(["last_fill_intent_id"], ["order_intents.id"], ondelete="RESTRICT"),
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

published_risk_policies = Table(
    "published_risk_policies",
    metadata,
    Column("policy_fingerprint", String(71), primary_key=True),
    Column("policy_id", String(36), nullable=False),
    Column("version", Integer(), nullable=False),
    Column("canonical_definition", Text(), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("policy_id", "version", name="ux_published_risk_policy_identity_version"),
    CheckConstraint("version > 0", name="ck_published_risk_policy_version_positive"),
    CheckConstraint(
        "policy_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_published_risk_policy_fingerprint_format",
    ),
)

active_risk_policy = Table(
    "active_risk_policy",
    metadata,
    Column("id", Integer(), primary_key=True),
    Column("policy_fingerprint", String(71), nullable=False),
    Column("activated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["policy_fingerprint"],
        ["published_risk_policies.policy_fingerprint"],
        ondelete="RESTRICT",
    ),
    CheckConstraint("id = 1", name="ck_active_risk_policy_singleton"),
)

experiential_journal_entries = Table(
    "experiential_journal_entries",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("origin", String(8), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("title", String(200), nullable=False),
    Column("body", Text(), nullable=False),
    Column("evidence_kind", String(16), nullable=False),
    Column("evidence_id", String(128), nullable=True),
    Column("product_id", String(32), nullable=True),
    Column("runtime_mode", String(16), nullable=False),
    Column("lesson_outcome", String(16), nullable=False),
    CheckConstraint("origin IN ('human', 'agent')", name="ck_experiential_journal_origin"),
    CheckConstraint(
        "kind IN ('fact', 'lesson', 'note')",
        name="ck_experiential_journal_kind",
    ),
    CheckConstraint(
        "evidence_kind IN ("
        "'none', 'backtest', 'paper_fill', 'live_fill', 'deployment', 'research', 'market_data'"
        ")",
        name="ck_experiential_journal_evidence_kind",
    ),
    CheckConstraint(
        "runtime_mode IN ('none', 'research', 'paper', 'live')",
        name="ck_experiential_journal_runtime_mode",
    ),
    CheckConstraint(
        "lesson_outcome IN ('none', 'success', 'mistake', 'mixed')",
        name="ck_experiential_journal_lesson_outcome",
    ),
)

Index(
    "ix_experiential_journal_occurred_at_desc",
    experiential_journal_entries.c.occurred_at.desc(),
    experiential_journal_entries.c.id.desc(),
)

experiential_sentiment_snapshots = Table(
    "experiential_sentiment_snapshots",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("origin", String(8), nullable=False),
    Column("label", String(16), nullable=False),
    Column("product_id", String(32), nullable=True),
    Column("note", Text(), nullable=False, server_default=""),
    Column("journal_id", UUID(), nullable=True),
    CheckConstraint("origin IN ('human', 'agent')", name="ck_experiential_sentiment_origin"),
    CheckConstraint(
        "label IN ('bullish', 'bearish', 'neutral', 'unknown')",
        name="ck_experiential_sentiment_label",
    ),
)

Index(
    "ix_experiential_sentiment_occurred_at_desc",
    experiential_sentiment_snapshots.c.occurred_at.desc(),
    experiential_sentiment_snapshots.c.id.desc(),
)

experiential_pattern_observations = Table(
    "experiential_pattern_observations",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("origin", String(8), nullable=False),
    Column("pattern_key", String(63), nullable=False),
    Column("name", String(120), nullable=False),
    Column("hypothesis", Text(), nullable=False),
    Column("status", String(16), nullable=False),
    Column("evidence_kind", String(16), nullable=False),
    Column("evidence_id", String(128), nullable=True),
    Column("note", Text(), nullable=False, server_default=""),
    CheckConstraint("origin IN ('human', 'agent')", name="ck_experiential_pattern_origin"),
    CheckConstraint(
        "status IN ('hypothesized', 'supported', 'contradicted', 'retired')",
        name="ck_experiential_pattern_status",
    ),
    CheckConstraint(
        "evidence_kind IN ("
        "'none', 'backtest', 'paper_fill', 'live_fill', 'deployment', 'research', 'market_data'"
        ")",
        name="ck_experiential_pattern_evidence_kind",
    ),
)

Index(
    "ix_experiential_pattern_occurred_at_desc",
    experiential_pattern_observations.c.occurred_at.desc(),
    experiential_pattern_observations.c.id.desc(),
)

experiential_notifications = Table(
    "experiential_notifications",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("origin", String(8), nullable=False),
    Column("title", String(200), nullable=False),
    Column("body", Text(), nullable=False),
    Column("severity", String(16), nullable=False),
    Column("provider", String(16), nullable=False),
    Column("delivery_status", String(16), nullable=False),
    Column("detail", String(500), nullable=False, server_default=""),
    Column("journal_id", UUID(), nullable=True),
    CheckConstraint("origin IN ('human', 'agent')", name="ck_experiential_notification_origin"),
    CheckConstraint(
        "severity IN ('info', 'warning', 'error')",
        name="ck_experiential_notification_severity",
    ),
    CheckConstraint(
        "provider IN ('none', 'log', 'webhook')",
        name="ck_experiential_notification_provider",
    ),
    CheckConstraint(
        "delivery_status IN ('skipped', 'logged', 'delivered', 'failed')",
        name="ck_experiential_notification_delivery_status",
    ),
)

Index(
    "ix_experiential_notifications_occurred_at_desc",
    experiential_notifications.c.occurred_at.desc(),
    experiential_notifications.c.id.desc(),
)

experiential_models = Table(
    "experiential_models",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("origin", String(8), nullable=False),
    Column("engine_id", String(64), nullable=False),
    Column("seed", Integer(), nullable=False),
    Column("fingerprint", String(71), nullable=False),
    Column("canonical_document", Text(), nullable=False),
    CheckConstraint("origin IN ('human', 'agent')", name="ck_experiential_models_origin"),
    CheckConstraint(
        "engine_id = 'thytrader-experiential-train-v1'",
        name="ck_experiential_models_engine_id",
    ),
    CheckConstraint("seed >= 0", name="ck_experiential_models_seed"),
    CheckConstraint(
        "fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_experiential_models_fingerprint",
    ),
    UniqueConstraint("fingerprint", name="uq_experiential_models_fingerprint"),
)

Index(
    "ix_experiential_models_recorded_at_desc",
    experiential_models.c.recorded_at.desc(),
    experiential_models.c.id.desc(),
)

trade_reason_records = Table(
    "trade_reason_records",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("origin", String(8), nullable=False),
    Column("intent_id", UUID(), nullable=False),
    Column("deployment_id", UUID(), nullable=False),
    Column("deployment_kind", String(16), nullable=False),
    Column("mode", String(8), nullable=False),
    Column("product_id", String(32), nullable=False),
    Column("purpose", String(32), nullable=False),
    Column("side", String(8), nullable=False),
    Column("strategy_id", UUID(), nullable=True),
    Column("strategy_fingerprint", String(71), nullable=True),
    Column("strategy_name", String(120), nullable=True),
    Column("strategy_version", Integer(), nullable=True),
    Column("signal_kind", String(32), nullable=False),
    Column("last_signal", String(32), nullable=True),
    Column("candle_starts_at", DateTime(timezone=True), nullable=False),
    Column("timeframe", String(8), nullable=True),
    Column("risk_decision", String(8), nullable=False),
    Column("risk_reason_code", String(64), nullable=False),
    Column("risk_detail", String(500), nullable=False),
    Column("policy_fingerprint", String(71), nullable=False),
    Column("policy_source", String(24), nullable=False),
    Column("notes_json", Text(), nullable=False, server_default="[]"),
    UniqueConstraint("intent_id", name="ux_trade_reason_records_intent_id"),
    CheckConstraint(
        "origin IN ('human', 'agent', 'runtime')",
        name="ck_trade_reason_origin",
    ),
    CheckConstraint(
        "deployment_kind IN ('strategy', 'discretionary')",
        name="ck_trade_reason_deployment_kind",
    ),
    CheckConstraint("mode IN ('paper', 'live')", name="ck_trade_reason_mode"),
    CheckConstraint("side IN ('buy', 'sell')", name="ck_trade_reason_side"),
    CheckConstraint(
        "purpose IN ('entry', 'take_profit', 'stop', 'time_exit', 'bracket')",
        name="ck_trade_reason_purpose",
    ),
    CheckConstraint(
        "signal_kind IN ("
        "'strategy_entry', 'discretionary', 'take_profit', 'stop', 'time_exit', 'bracket'"
        ")",
        name="ck_trade_reason_signal_kind",
    ),
    CheckConstraint(
        "risk_decision IN ('allow', 'deny')",
        name="ck_trade_reason_risk_decision",
    ),
    CheckConstraint(
        "policy_source IN ('compiled_default', 'published')",
        name="ck_trade_reason_policy_source",
    ),
    CheckConstraint(
        "timeframe IS NULL OR timeframe IN "
        "('1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d')",
        name="ck_trade_reason_timeframe",
    ),
)

Index(
    "ix_trade_reason_created_at_desc",
    trade_reason_records.c.created_at.desc(),
    trade_reason_records.c.id.desc(),
)

Index(
    "ix_trade_reason_deployment_created_at_desc",
    trade_reason_records.c.deployment_id,
    trade_reason_records.c.created_at.desc(),
)

__all__ = [
    "active_risk_policy",
    "archived_strategy_versions",
    "audit_events",
    "deployments",
    "execution_fills",
    "execution_instrument_state",
    "execution_orders",
    "execution_positions",
    "experiential_journal_entries",
    "experiential_models",
    "experiential_notifications",
    "experiential_pattern_observations",
    "experiential_sentiment_snapshots",
    "market_data_watchlist",
    "market_data_worker_state",
    "market_feed_state",
    "metadata",
    "order_intents",
    "portfolio_snapshots",
    "published_backtest_results",
    "published_research_run_specs",
    "published_research_studies",
    "published_risk_policies",
    "published_strategy_versions",
    "strategy_dataset_bindings",
    "strategy_drafts",
    "trade_reason_records",
    "user_order_feed_state",
]
