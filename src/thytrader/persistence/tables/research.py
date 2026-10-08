"""Published research runs, backtest results, research jobs/workers, and studies tables."""

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
    String,
    Table,
    Text,
)

from thytrader.persistence.schema_metadata import metadata

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
    Column("metrics_json", Text(), nullable=True),
    Column("cost_attribution_json", Text(), nullable=True),
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

__all__ = [
    "published_backtest_results",
    "published_research_run_specs",
    "published_research_studies",
    "research_jobs",
    "research_study_strategies",
    "research_workers",
]
