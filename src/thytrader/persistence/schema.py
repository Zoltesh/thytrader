"""SQLAlchemy Core metadata for append-only operational records."""

from __future__ import annotations

from sqlalchemy import (
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

published_strategy_versions = Table(
    "published_strategy_versions",
    metadata,
    Column("strategy_fingerprint", String(71), primary_key=True),
    Column("strategy_id", String(36), nullable=False),
    Column("version", Integer(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("canonical_definition", Text(), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
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

__all__ = [
    "market_data_worker_state",
    "metadata",
    "portfolio_snapshots",
    "published_backtest_results",
    "published_research_run_specs",
    "published_strategy_versions",
    "strategy_dataset_bindings",
]
