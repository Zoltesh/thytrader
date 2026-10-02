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

_FINGERPRINT_REGEX = "'^sha256:[0-9a-f]{64}$'"

strategies = Table(
    "strategies",
    metadata,
    Column("strategy_id", String(36), primary_key=True, comment="UUIDv7 strategy identity."),
    Column("name", String(120), nullable=False),
    Column("product_id", String(32), nullable=True, comment="Primary product when parseable."),
    Column("timeframe", String(8), nullable=True, comment="Decision clock when parseable."),
    Column("document", Text(), nullable=False, comment="Canonical JSON when valid, else sorted."),
    Column("is_valid", Boolean(), nullable=False),
    Column("validation_issues", Text(), nullable=False, server_default="[]"),
    Column("current_fingerprint", String(71), nullable=True),
    Column("revision", BigInteger(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("revision > 0", name="ck_strategies_revision_positive"),
    CheckConstraint(
        "(is_valid AND current_fingerprint IS NOT NULL) "
        "OR (NOT is_valid AND current_fingerprint IS NULL)",
        name="ck_strategies_validity_fingerprint",
    ),
    CheckConstraint(
        f"current_fingerprint IS NULL OR current_fingerprint ~ {_FINGERPRINT_REGEX}",
        name="ck_strategies_current_fingerprint_format",
    ),
)

Index(
    "ix_strategies_updated",
    strategies.c.updated_at.desc(),
    strategies.c.strategy_id.asc(),
)

strategy_snapshots = Table(
    "strategy_snapshots",
    metadata,
    Column("strategy_fingerprint", String(71), primary_key=True),
    Column(
        "strategy_id",
        String(36),
        nullable=True,
        comment="Owning strategy; NULL only for snapshots a kept live deployment ran.",
    ),
    Column("canonical_definition", Text(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["strategy_id"],
        ["strategies.strategy_id"],
        ondelete="SET NULL",
        name="fk_strategy_snapshots_strategy_id",
    ),
    CheckConstraint(
        f"strategy_fingerprint ~ {_FINGERPRINT_REGEX}",
        name="ck_strategy_snapshots_fingerprint_format",
    ),
)

Index("ix_strategy_snapshots_strategy_id", strategy_snapshots.c.strategy_id)

strategy_dataset_bindings = Table(
    "strategy_dataset_bindings",
    metadata,
    Column("strategy_fingerprint", String(71), primary_key=True),
    Column("dataset_fingerprint", String(71), primary_key=True),
    Column("strategy_id", String(36), nullable=False),
    Column("bound_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["strategy_fingerprint"],
        ["strategy_snapshots.strategy_fingerprint"],
        name="fk_strategy_dataset_bindings_snapshot",
    ),
    ForeignKeyConstraint(
        ["strategy_id"],
        ["strategies.strategy_id"],
        ondelete="CASCADE",
        name="fk_strategy_dataset_bindings_strategy_id",
    ),
    CheckConstraint(
        f"strategy_fingerprint ~ {_FINGERPRINT_REGEX}",
        name="ck_strategy_dataset_binding_strategy_fingerprint_format",
    ),
    CheckConstraint(
        f"dataset_fingerprint ~ {_FINGERPRINT_REGEX}",
        name="ck_strategy_dataset_binding_dataset_fingerprint_format",
    ),
)

Index("ix_strategy_dataset_bindings_strategy_id", strategy_dataset_bindings.c.strategy_id)

published_research_run_specs = Table(
    "published_research_run_specs",
    metadata,
    Column("run_fingerprint", String(71), primary_key=True),
    Column("run_id", String(36), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("strategy_fingerprint", String(71), nullable=False),
    Column("strategy_id", String(36), nullable=False),
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
        name="fk_research_run_specs_binding",
    ),
    ForeignKeyConstraint(
        ["strategy_id"],
        ["strategies.strategy_id"],
        ondelete="CASCADE",
        name="fk_research_run_specs_strategy_id",
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
    Column("strategy_id", String(36), nullable=False),
    Column("dataset_fingerprint", String(71), nullable=False),
    Column("signal_trace_fingerprint", String(71), nullable=False),
    Column("canonical_result", Text(), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column(
        "diagnostics_json",
        Text(),
        nullable=True,
        comment=(
            "thytrader-backtest-diagnostics-v1 entry-funnel counters; outside the "
            "canonical result bytes and fingerprint (ADR 0090)."
        ),
    ),
    ForeignKeyConstraint(
        ["run_fingerprint"],
        ["published_research_run_specs.run_fingerprint"],
        name="fk_backtest_results_run",
    ),
    ForeignKeyConstraint(
        ["strategy_id"],
        ["strategies.strategy_id"],
        ondelete="CASCADE",
        name="fk_backtest_results_strategy_id",
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
    "ix_published_research_run_specs_strategy_id",
    published_research_run_specs.c.strategy_id,
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
    "ix_published_backtest_results_strategy_id_published",
    published_backtest_results.c.strategy_id,
    published_backtest_results.c.published_at.desc(),
    published_backtest_results.c.result_fingerprint.asc(),
)

Index(
    "ix_published_backtest_results_dataset_fingerprint",
    published_backtest_results.c.dataset_fingerprint,
)

Index(
    "ix_published_backtest_results_published_result",
    published_backtest_results.c.published_at.desc(),
    published_backtest_results.c.result_fingerprint.asc(),
)

research_jobs = Table(
    "research_jobs",
    metadata,
    Column("job_id", UUID(), primary_key=True),
    Column("kind", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("strategy_id", String(36), nullable=False),
    Column("strategy_fingerprint", String(71), nullable=False),
    Column("payload", Text(), nullable=False),
    Column("progress_current", Integer(), nullable=False, server_default="0"),
    Column("progress_total", Integer(), nullable=False, server_default="0"),
    Column("error_message", String(256), nullable=True),
    Column("failed_phase", String(32), nullable=True),
    Column("failed_detail", String(500), nullable=True),
    Column("run_fingerprint", String(71), nullable=True),
    Column("result_fingerprint", String(71), nullable=True),
    Column("study_fingerprint", String(71), nullable=True),
    Column("plan_fingerprint", String(71), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("cancel_requested", Boolean(), nullable=False, server_default="false"),
    Column(
        "error_code",
        String(32),
        nullable=True,
        comment="ResearchJobErrorCode of a failed job (ADR 0092).",
    ),
    Column(
        "lease_owner",
        String(96),
        nullable=True,
        comment="Research worker token holding a running job (ADR 0092).",
    ),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("attempts", Integer(), nullable=False, server_default="0"),
    ForeignKeyConstraint(
        ["strategy_id"],
        ["strategies.strategy_id"],
        ondelete="CASCADE",
        name="fk_research_jobs_strategy_id",
    ),
    CheckConstraint("kind IN ('backtest', 'study')", name="ck_research_jobs_kind"),
    CheckConstraint("attempts >= 0", name="ck_research_jobs_attempts"),
    CheckConstraint(
        "status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'expired')",
        name="ck_research_jobs_status",
    ),
    CheckConstraint("progress_current >= 0", name="ck_research_jobs_progress_current"),
    CheckConstraint("progress_total >= 0", name="ck_research_jobs_progress_total"),
)

Index(
    "ix_research_jobs_status_created",
    research_jobs.c.status,
    research_jobs.c.created_at.asc(),
)

Index(
    "ix_research_jobs_strategy_created",
    research_jobs.c.strategy_id,
    research_jobs.c.created_at.desc(),
)

research_workers = Table(
    "research_workers",
    metadata,
    Column(
        "slot", Integer(), primary_key=True, autoincrement=False, comment="Supervisor slot index."
    ),
    Column("pool_size", Integer(), nullable=False, comment="Configured research workers."),
    Column("pid", Integer(), nullable=False),
    Column("state", String(16), nullable=False),
    Column("job_id", UUID(), nullable=True),
    Column("job_kind", String(32), nullable=True),
    Column("jobs_completed", Integer(), nullable=False, server_default="0"),
    Column("rss_bytes", BigInteger(), nullable=True, comment="Resident set size of the process."),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("heartbeat_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("slot >= 0 AND pool_size >= 1 AND pid >= 1", name="ck_research_workers_ids"),
    CheckConstraint(
        "state IN ('starting', 'idle', 'running', 'stopping')", name="ck_research_workers_state"
    ),
    CheckConstraint(
        "job_kind IS NULL OR job_kind IN ('backtest', 'study', 'portfolio_backtest')",
        name="ck_research_workers_job_kind",
    ),
    CheckConstraint(
        "jobs_completed >= 0 AND (rss_bytes IS NULL OR rss_bytes >= 0)",
        name="ck_research_workers_counts",
    ),
    comment="Latest self-report of each research worker process (ADR 0092).",
)

published_research_studies = Table(
    "published_research_studies",
    metadata,
    Column("study_fingerprint", String(71), primary_key=True),
    Column("strategy_id", String(36), nullable=False, comment="Primary (first) strategy."),
    Column("request_fingerprint", String(71), nullable=False),
    Column("plan_fingerprint", String(71), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("product_id", String(32), nullable=False),
    Column("timeframe", String(8), nullable=False),
    Column("window_count", Integer(), nullable=False),
    Column("selected_strategy_fingerprint", String(71), nullable=True),
    Column("mean_oos_return_fraction", String(64), nullable=True),
    Column("stitched_oos_available", Boolean(), nullable=True),
    Column("selection_metric", String(64), nullable=True),
    Column("canonical_study", Text(), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["strategy_id"],
        ["strategies.strategy_id"],
        ondelete="CASCADE",
        name="fk_research_studies_strategy_id",
    ),
    CheckConstraint(
        "study_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_research_study_fingerprint_format",
    ),
    CheckConstraint(
        "request_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_research_study_request_fingerprint_format",
    ),
    CheckConstraint(
        "plan_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_research_study_plan_fingerprint_format",
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

Index(
    "ix_published_research_studies_plan_fingerprint",
    published_research_studies.c.plan_fingerprint,
    unique=True,
)

Index(
    "ix_published_research_studies_strategy_published",
    published_research_studies.c.strategy_id,
    published_research_studies.c.published_at.desc(),
)

research_study_strategies = Table(
    "research_study_strategies",
    metadata,
    Column("study_fingerprint", String(71), primary_key=True),
    Column("strategy_id", String(36), primary_key=True),
    ForeignKeyConstraint(
        ["study_fingerprint"],
        ["published_research_studies.study_fingerprint"],
        ondelete="CASCADE",
        name="fk_research_study_strategies_study",
    ),
    ForeignKeyConstraint(
        ["strategy_id"],
        ["strategies.strategy_id"],
        ondelete="CASCADE",
        name="fk_research_study_strategies_strategy_id",
    ),
)

Index("ix_research_study_strategies_strategy_id", research_study_strategies.c.strategy_id)

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
    Column("initial_equity", String(64), nullable=True),
    Column("baseline_equity", String(64), nullable=True),
    Column("utc_day_open_equity", String(64), nullable=True),
    Column("utc_day_open_at", DateTime(timezone=True), nullable=True),
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
        "strategy_id IS NOT NULL OR (mode = 'live' AND status = 'stopped'))"
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
    Column(
        "target_price",
        String(64),
        nullable=True,
        comment="Take-profit price; NULL when the strategy declares no take-profit (ADR 0090).",
    ),
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
        "purpose IN ('entry', 'take_profit', 'stop', 'time_exit', 'bracket', 'signal_exit')",
        name="ck_trade_reason_purpose",
    ),
    CheckConstraint(
        "signal_kind IN ("
        "'strategy_entry', 'discretionary', 'take_profit', 'stop', 'time_exit', 'bracket', "
        "'signal_exit'"
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

bar_decisions = Table(
    "bar_decisions",
    metadata,
    Column("deployment_id", UUID(), primary_key=True),
    Column("product_id", String(32), primary_key=True),
    Column(
        "bar_starts_at",
        DateTime(timezone=True),
        primary_key=True,
        comment="UTC start of the completed decision bar this row explains.",
    ),
    Column("strategy_id", UUID(), nullable=True),
    Column("mode", String(8), nullable=False),
    Column("timeframe", String(8), nullable=False),
    Column("evaluated_at", DateTime(timezone=True), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("action", String(24), nullable=False),
    Column("reason_code", String(64), nullable=False),
    Column("intent_id", UUID(), nullable=True),
    Column("summary", String(500), nullable=False),
    Column(
        "payload_json",
        Text(),
        nullable=False,
        comment="Canonical thytrader-bar-decision-v1 JSON (rule tree, risk, orders, fills).",
    ),
    ForeignKeyConstraint(
        ["deployment_id"],
        ["deployments.id"],
        ondelete="CASCADE",
        name="fk_bar_decisions_deployment_id",
    ),
    CheckConstraint("mode IN ('paper', 'live')", name="ck_bar_decisions_mode"),
    CheckConstraint(
        "outcome IN ('entry_signal', 'no_signal', 'holding', 'exit', 'entry_blocked', "
        "'skipped', 'error')",
        name="ck_bar_decisions_outcome",
    ),
    CheckConstraint(
        "action IN ('none', 'intent_created', 'order_submitted', 'order_canceled', 'repriced')",
        name="ck_bar_decisions_action",
    ),
    CheckConstraint(
        "timeframe IN ('1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d')",
        name="ck_bar_decisions_timeframe",
    ),
    comment="Bounded per-bar decision journal for paper and live strategy bots (ADR 0087).",
)

Index(
    "ix_bar_decisions_deployment_bar_desc",
    bar_decisions.c.deployment_id,
    bar_decisions.c.bar_starts_at.desc(),
    bar_decisions.c.product_id.desc(),
)

Index(
    "ix_bar_decisions_strategy_bar_desc",
    bar_decisions.c.strategy_id,
    bar_decisions.c.bar_starts_at.desc(),
)

Index("ix_bar_decisions_bar_starts_at", bar_decisions.c.bar_starts_at)

_FRACTION_REGEX = "'^(0|[1-9][0-9]*)([.][0-9]{1,4})?$'"
_QUOTE_REGEX = "'^(0|[1-9][0-9]*)([.][0-9]{1,8})?$'"

portfolios = Table(
    "portfolios",
    metadata,
    Column("portfolio_id", String(36), primary_key=True, comment="UUIDv7 portfolio identity."),
    Column("name", String(120), nullable=False),
    Column("mode", String(8), nullable=False, comment="paper or live; fixed at creation."),
    Column("quote_currency", String(8), nullable=False, comment="Fixed at creation."),
    Column("capital_quote", String(64), nullable=False, comment="Canonical decimal text."),
    Column("cash_reserve_fraction", String(16), nullable=False),
    Column("max_total_exposure_fraction", String(16), nullable=False),
    Column("max_per_asset_fraction", String(16), nullable=False),
    Column("daily_loss_quote", String(64), nullable=True),
    Column("max_drawdown_fraction", String(16), nullable=True),
    Column("manager_mandate", Text(), nullable=False, server_default=""),
    Column("manager_may_rebalance", Boolean(), nullable=False, server_default="false"),
    Column("manager_max_weight_change_per_week", String(16), nullable=False),
    Column("manager_may_pause_sleeves", Boolean(), nullable=False, server_default="false"),
    Column("manager_may_propose_sleeves", Boolean(), nullable=False, server_default="false"),
    Column("revision", BigInteger(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("mode IN ('paper', 'live')", name="ck_portfolios_mode"),
    CheckConstraint("quote_currency IN ('USD', 'USDC', 'USDT')", name="ck_portfolios_quote"),
    CheckConstraint("revision > 0", name="ck_portfolios_revision_positive"),
    CheckConstraint(f"capital_quote ~ {_QUOTE_REGEX}", name="ck_portfolios_capital_format"),
    CheckConstraint(
        f"cash_reserve_fraction ~ {_FRACTION_REGEX}", name="ck_portfolios_reserve_format"
    ),
    CheckConstraint(
        f"max_total_exposure_fraction ~ {_FRACTION_REGEX} "
        f"AND max_per_asset_fraction ~ {_FRACTION_REGEX} "
        f"AND manager_max_weight_change_per_week ~ {_FRACTION_REGEX}",
        name="ck_portfolios_limit_formats",
    ),
    CheckConstraint(
        f"(daily_loss_quote IS NULL OR daily_loss_quote ~ {_QUOTE_REGEX}) "
        f"AND (max_drawdown_fraction IS NULL OR max_drawdown_fraction ~ {_FRACTION_REGEX})",
        name="ck_portfolios_optional_limit_formats",
    ),
)

Index("ix_portfolios_created", portfolios.c.created_at.asc(), portfolios.c.portfolio_id.asc())

portfolio_sleeves = Table(
    "portfolio_sleeves",
    metadata,
    Column("sleeve_id", String(36), primary_key=True, comment="UUIDv7 sleeve identity."),
    Column("portfolio_id", String(36), nullable=False),
    Column("strategy_id", String(36), nullable=False),
    Column("weight_fraction", String(16), nullable=False),
    Column("note", String(280), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["portfolio_id"],
        ["portfolios.portfolio_id"],
        ondelete="CASCADE",
        name="fk_portfolio_sleeves_portfolio_id",
    ),
    ForeignKeyConstraint(
        ["strategy_id"],
        ["strategies.strategy_id"],
        ondelete="CASCADE",
        name="fk_portfolio_sleeves_strategy_id",
    ),
    UniqueConstraint("portfolio_id", "strategy_id", name="ux_portfolio_sleeves_strategy"),
    CheckConstraint(
        f"weight_fraction ~ {_FRACTION_REGEX}", name="ck_portfolio_sleeves_weight_format"
    ),
)

Index("ix_portfolio_sleeves_strategy_id", portfolio_sleeves.c.strategy_id)

portfolio_journal_entries = Table(
    "portfolio_journal_entries",
    metadata,
    Column(
        "sequence",
        BigInteger(),
        primary_key=True,
        autoincrement=True,
        comment="Monotonic append order.",
    ),
    Column("entry_id", UUID(), nullable=False, unique=True),
    Column("portfolio_id", String(36), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("actor", String(16), nullable=False),
    Column("channel", String(16), nullable=False),
    Column("summary", String(500), nullable=False),
    Column("detail", Text(), nullable=False, comment="Canonical JournalDetail JSON."),
    Column("revision", BigInteger(), nullable=False),
    ForeignKeyConstraint(
        ["portfolio_id"],
        ["portfolios.portfolio_id"],
        ondelete="CASCADE",
        name="fk_portfolio_journal_portfolio_id",
    ),
    CheckConstraint(
        "kind IN ("
        "'created', 'settings_changed', 'sleeve_added', 'sleeve_updated', 'sleeve_removed', "
        "'weights_changed', 'limits_changed', 'manager_changed', 'backtest_run', "
        "'deployment_started', 'deployment_paused', 'deployment_resumed', "
        "'deployment_stopped', 'breaker_tripped', 'breaker_reset', 'proposal_submitted', "
        "'proposal_approved', 'proposal_declined', 'proposal_failed'"
        ")",
        name="ck_portfolio_journal_kind",
    ),
    CheckConstraint(
        "actor IN ('operator', 'system', 'manager')", name="ck_portfolio_journal_actor"
    ),
    CheckConstraint("channel IN ('browser', 'api', 'system')", name="ck_portfolio_journal_channel"),
    CheckConstraint("revision > 0", name="ck_portfolio_journal_revision_positive"),
)

Index(
    "ix_portfolio_journal_portfolio_sequence",
    portfolio_journal_entries.c.portfolio_id,
    portfolio_journal_entries.c.sequence.desc(),
)

portfolio_backtest_jobs = Table(
    "portfolio_backtest_jobs",
    metadata,
    Column("job_id", UUID(), primary_key=True),
    Column("portfolio_id", String(36), nullable=False),
    Column("portfolio_revision", BigInteger(), nullable=False),
    Column("status", String(16), nullable=False),
    Column("payload", Text(), nullable=False, comment="Resolved PortfolioBacktestPlan JSON."),
    Column("actor", String(16), nullable=False),
    Column("channel", String(16), nullable=False),
    Column("evaluation_start", DateTime(timezone=True), nullable=False),
    Column("evaluation_end", DateTime(timezone=True), nullable=False),
    Column("sleeve_count", Integer(), nullable=False),
    Column("progress_current", Integer(), nullable=False, server_default="0"),
    Column("progress_total", Integer(), nullable=False, server_default="0"),
    Column("error_message", String(256), nullable=True),
    Column("failed_detail", String(500), nullable=True),
    Column("result_fingerprint", String(71), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column(
        "lease_owner",
        String(96),
        nullable=True,
        comment="Research worker token holding a running job (ADR 0092).",
    ),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("attempts", Integer(), nullable=False, server_default="0"),
    ForeignKeyConstraint(
        ["portfolio_id"],
        ["portfolios.portfolio_id"],
        ondelete="CASCADE",
        name="fk_portfolio_backtest_jobs_portfolio_id",
    ),
    CheckConstraint("attempts >= 0", name="ck_portfolio_backtest_jobs_attempts"),
    CheckConstraint(
        "status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'expired')",
        name="ck_portfolio_backtest_jobs_status",
    ),
    CheckConstraint(
        "actor IN ('operator', 'system', 'manager')", name="ck_portfolio_backtest_jobs_actor"
    ),
    CheckConstraint(
        "channel IN ('browser', 'api', 'system')", name="ck_portfolio_backtest_jobs_channel"
    ),
    CheckConstraint(
        "progress_current >= 0 AND progress_total >= 0 AND sleeve_count > 0",
        name="ck_portfolio_backtest_jobs_counts",
    ),
    CheckConstraint(
        f"result_fingerprint IS NULL OR result_fingerprint ~ {_FINGERPRINT_REGEX}",
        name="ck_portfolio_backtest_jobs_result_format",
    ),
)

Index(
    "ix_portfolio_backtest_jobs_status_created",
    portfolio_backtest_jobs.c.status,
    portfolio_backtest_jobs.c.created_at.asc(),
)

Index(
    "ix_portfolio_backtest_jobs_portfolio_created",
    portfolio_backtest_jobs.c.portfolio_id,
    portfolio_backtest_jobs.c.created_at.desc(),
)

published_portfolio_backtests = Table(
    "published_portfolio_backtests",
    metadata,
    Column("result_fingerprint", String(71), primary_key=True),
    Column("portfolio_id", String(36), nullable=False),
    Column("portfolio_revision", BigInteger(), nullable=False),
    Column("evaluation_start", DateTime(timezone=True), nullable=False),
    Column("evaluation_end", DateTime(timezone=True), nullable=False),
    Column("listing", Text(), nullable=False, comment="PortfolioBacktestListing JSON."),
    Column("canonical_result", Text(), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["portfolio_id"],
        ["portfolios.portfolio_id"],
        ondelete="CASCADE",
        name="fk_published_portfolio_backtests_portfolio_id",
    ),
    CheckConstraint(
        f"result_fingerprint ~ {_FINGERPRINT_REGEX}",
        name="ck_published_portfolio_backtests_fingerprint_format",
    ),
)

Index(
    "ix_published_portfolio_backtests_portfolio_published",
    published_portfolio_backtests.c.portfolio_id,
    published_portfolio_backtests.c.published_at.desc(),
    published_portfolio_backtests.c.result_fingerprint.asc(),
)

_SIGNED_DECIMAL_REGEX = "'^-?(0|[1-9][0-9]*)([.][0-9]+)?$'"

portfolio_runtime = Table(
    "portfolio_runtime",
    metadata,
    Column("portfolio_id", String(36), primary_key=True),
    Column("run_started_at", DateTime(timezone=True), nullable=True),
    Column("breaker_reason", String(48), nullable=True),
    Column("breaker_detail", String(500), nullable=True),
    Column("breaker_latched_at", DateTime(timezone=True), nullable=True),
    Column("day_open_equity", String(64), nullable=True),
    Column("day_open_at", DateTime(timezone=True), nullable=True),
    Column("high_water_mark_equity", String(64), nullable=True),
    Column("last_equity", String(64), nullable=True),
    Column("last_evaluated_at", DateTime(timezone=True), nullable=True),
    Column("revision", BigInteger(), nullable=False, comment="Compare-and-set write counter."),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["portfolio_id"],
        ["portfolios.portfolio_id"],
        ondelete="CASCADE",
        name="fk_portfolio_runtime_portfolio_id",
    ),
    CheckConstraint("revision > 0", name="ck_portfolio_runtime_revision_positive"),
    CheckConstraint(
        "breaker_reason IS NULL OR breaker_reason IN "
        "('PORTFOLIO_DAILY_LOSS_STOP', 'PORTFOLIO_DRAWDOWN_STOP')",
        name="ck_portfolio_runtime_breaker_reason",
    ),
    CheckConstraint(
        "(breaker_reason IS NULL) = (breaker_latched_at IS NULL)",
        name="ck_portfolio_runtime_breaker_latch",
    ),
    CheckConstraint(
        f"(day_open_equity IS NULL OR day_open_equity ~ {_SIGNED_DECIMAL_REGEX}) "
        f"AND (high_water_mark_equity IS NULL OR high_water_mark_equity ~ "
        f"{_SIGNED_DECIMAL_REGEX}) "
        f"AND (last_equity IS NULL OR last_equity ~ {_SIGNED_DECIMAL_REGEX})",
        name="ck_portfolio_runtime_equity_formats",
    ),
)

portfolio_proposals = Table(
    "portfolio_proposals",
    metadata,
    Column("proposal_id", UUID(), primary_key=True),
    Column("portfolio_id", String(36), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("summary", String(500), nullable=False),
    Column("rationale", Text(), nullable=False),
    Column("change", Text(), nullable=False, comment="Canonical ProposalChange JSON."),
    Column("evidence", Text(), nullable=False, comment="Canonical evidence JSON array."),
    Column("base_revision", BigInteger(), nullable=False),
    Column("submitted_by", String(16), nullable=False),
    Column("channel", String(16), nullable=False),
    Column("approval_reason", String(500), nullable=True),
    Column("weight_moved", String(16), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("decided_at", DateTime(timezone=True), nullable=True),
    Column("decided_by", String(16), nullable=True),
    Column("decision_note", String(500), nullable=True),
    Column("auto_applied", Boolean(), nullable=False, server_default="false"),
    Column("applied_revision", BigInteger(), nullable=True),
    Column("failure_code", String(64), nullable=True),
    Column("failure_message", String(500), nullable=True),
    ForeignKeyConstraint(
        ["portfolio_id"],
        ["portfolios.portfolio_id"],
        ondelete="CASCADE",
        name="fk_portfolio_proposals_portfolio_id",
    ),
    CheckConstraint(
        "kind IN ('rebalance', 'pause_sleeve', 'resume_sleeve', 'add_sleeve')",
        name="ck_portfolio_proposals_kind",
    ),
    CheckConstraint(
        "status IN ('pending', 'applied', 'declined', 'failed', 'expired')",
        name="ck_portfolio_proposals_status",
    ),
    CheckConstraint(
        "submitted_by IN ('manager', 'operator')", name="ck_portfolio_proposals_submitted_by"
    ),
    CheckConstraint(
        "decided_by IS NULL OR decided_by IN ('operator', 'manager', 'system')",
        name="ck_portfolio_proposals_decided_by",
    ),
    CheckConstraint(
        f"weight_moved IS NULL OR weight_moved ~ {_FRACTION_REGEX}",
        name="ck_portfolio_proposals_weight_moved_format",
    ),
    CheckConstraint("base_revision > 0", name="ck_portfolio_proposals_base_revision_positive"),
)

Index(
    "ix_portfolio_proposals_portfolio_created",
    portfolio_proposals.c.portfolio_id,
    portfolio_proposals.c.created_at.desc(),
)

__all__ = [
    "active_risk_policy",
    "audit_events",
    "bar_decisions",
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
    "portfolio_backtest_jobs",
    "portfolio_journal_entries",
    "portfolio_proposals",
    "portfolio_runtime",
    "portfolio_sleeves",
    "portfolio_snapshots",
    "portfolios",
    "published_backtest_results",
    "published_portfolio_backtests",
    "published_research_run_specs",
    "published_research_studies",
    "published_risk_policies",
    "research_jobs",
    "research_study_strategies",
    "strategies",
    "strategy_dataset_bindings",
    "strategy_snapshots",
    "trade_reason_records",
    "user_order_feed_state",
]
