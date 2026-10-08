"""Portfolio definition, sleeve, journal, backtest, runtime, and proposal tables."""

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
    UniqueConstraint,
)

from thytrader.persistence.schema_metadata import FINGERPRINT_REGEX, metadata

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
        f"result_fingerprint IS NULL OR result_fingerprint ~ {FINGERPRINT_REGEX}",
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
        f"result_fingerprint ~ {FINGERPRINT_REGEX}",
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
    "portfolio_backtest_jobs",
    "portfolio_journal_entries",
    "portfolio_proposals",
    "portfolio_runtime",
    "portfolio_sleeves",
    "portfolios",
    "published_portfolio_backtests",
]
