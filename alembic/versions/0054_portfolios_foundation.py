"""Portfolios foundation: portfolios, sleeves, journal, and portfolio backtests.

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-02

ADR 0088. Additive only; no existing row changes:

* ``portfolios`` — one paper or live portfolio (mode and quote currency fixed at creation),
  its capital, cash reserve, shared limits, manager settings, and a revision counter.
* ``portfolio_sleeves`` — one strategy per sleeve with a capital weight. Deleting the
  portfolio or the strategy deletes the sleeve (the strategy-deletion transaction journals
  it first).
* ``portfolio_journal_entries`` — the append-only portfolio journal.
* ``portfolio_backtest_jobs`` and ``published_portfolio_backtests`` — the async portfolio
  backtest queue and its content-addressed results.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

_TZ = sa.DateTime(timezone=True)
_FP = "'^sha256:[0-9a-f]{64}$'"
_FRACTION = "'^(0|[1-9][0-9]*)([.][0-9]{1,4})?$'"
_QUOTE = "'^(0|[1-9][0-9]*)([.][0-9]{1,8})?$'"
_STATUSES = "('queued', 'running', 'completed', 'failed', 'cancelled', 'expired')"
_ACTORS = "('operator', 'system', 'manager')"
_CHANNELS = "('browser', 'api', 'system')"
_KINDS = (
    "('created', 'settings_changed', 'sleeve_added', 'sleeve_updated', 'sleeve_removed', "
    "'weights_changed', 'limits_changed', 'manager_changed', 'backtest_run')"
)


def upgrade() -> None:
    """Create the portfolio tables and their indexes."""
    _create_portfolios()
    _create_sleeves()
    _create_journal()
    _create_backtest_jobs()
    _create_backtest_results()


def downgrade() -> None:
    """Drop the portfolio tables (children first)."""
    op.drop_table("published_portfolio_backtests")
    op.drop_table("portfolio_backtest_jobs")
    op.drop_table("portfolio_journal_entries")
    op.drop_table("portfolio_sleeves")
    op.drop_table("portfolios")


def _portfolio_fk(table: str) -> sa.ForeignKeyConstraint:
    """Cascade one child table from its portfolio."""
    return sa.ForeignKeyConstraint(
        ["portfolio_id"],
        ["portfolios.portfolio_id"],
        ondelete="CASCADE",
        name=f"fk_{table}_portfolio_id",
    )


def _create_portfolios() -> None:
    """One row per portfolio."""
    op.create_table(
        "portfolios",
        sa.Column(
            "portfolio_id", sa.String(36), primary_key=True, comment="UUIDv7 portfolio identity."
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "mode", sa.String(8), nullable=False, comment="paper or live; fixed at creation."
        ),
        sa.Column("quote_currency", sa.String(8), nullable=False, comment="Fixed at creation."),
        sa.Column(
            "capital_quote", sa.String(64), nullable=False, comment="Canonical decimal text."
        ),
        sa.Column("cash_reserve_fraction", sa.String(16), nullable=False),
        sa.Column("max_total_exposure_fraction", sa.String(16), nullable=False),
        sa.Column("max_per_asset_fraction", sa.String(16), nullable=False),
        sa.Column("daily_loss_quote", sa.String(64), nullable=True),
        sa.Column("max_drawdown_fraction", sa.String(16), nullable=True),
        sa.Column("manager_mandate", sa.Text(), nullable=False, server_default=""),
        sa.Column("manager_may_rebalance", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("manager_max_weight_change_per_week", sa.String(16), nullable=False),
        sa.Column(
            "manager_may_pause_sleeves", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "manager_may_propose_sleeves", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
        sa.CheckConstraint("mode IN ('paper', 'live')", name="ck_portfolios_mode"),
        sa.CheckConstraint("quote_currency IN ('USD', 'USDC', 'USDT')", name="ck_portfolios_quote"),
        sa.CheckConstraint("revision > 0", name="ck_portfolios_revision_positive"),
        sa.CheckConstraint(f"capital_quote ~ {_QUOTE}", name="ck_portfolios_capital_format"),
        sa.CheckConstraint(
            f"cash_reserve_fraction ~ {_FRACTION}", name="ck_portfolios_reserve_format"
        ),
        sa.CheckConstraint(
            f"max_total_exposure_fraction ~ {_FRACTION} "
            f"AND max_per_asset_fraction ~ {_FRACTION} "
            f"AND manager_max_weight_change_per_week ~ {_FRACTION}",
            name="ck_portfolios_limit_formats",
        ),
        sa.CheckConstraint(
            f"(daily_loss_quote IS NULL OR daily_loss_quote ~ {_QUOTE}) "
            f"AND (max_drawdown_fraction IS NULL OR max_drawdown_fraction ~ {_FRACTION})",
            name="ck_portfolios_optional_limit_formats",
        ),
    )
    op.create_index(
        "ix_portfolios_created",
        "portfolios",
        ["created_at", "portfolio_id"],
    )


def _create_sleeves() -> None:
    """One strategy per sleeve, cascading from the portfolio and the strategy."""
    op.create_table(
        "portfolio_sleeves",
        sa.Column("sleeve_id", sa.String(36), primary_key=True, comment="UUIDv7 sleeve identity."),
        sa.Column("portfolio_id", sa.String(36), nullable=False),
        sa.Column("strategy_id", sa.String(36), nullable=False),
        sa.Column("weight_fraction", sa.String(16), nullable=False),
        sa.Column("note", sa.String(280), nullable=True),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.portfolio_id"],
            ondelete="CASCADE",
            name="fk_portfolio_sleeves_portfolio_id",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.strategy_id"],
            ondelete="CASCADE",
            name="fk_portfolio_sleeves_strategy_id",
        ),
        sa.UniqueConstraint("portfolio_id", "strategy_id", name="ux_portfolio_sleeves_strategy"),
        sa.CheckConstraint(
            f"weight_fraction ~ {_FRACTION}", name="ck_portfolio_sleeves_weight_format"
        ),
    )
    op.create_index("ix_portfolio_sleeves_strategy_id", "portfolio_sleeves", ["strategy_id"])


def _create_journal() -> None:
    """The append-only journal, ordered by a monotonic sequence."""
    op.create_table(
        "portfolio_journal_entries",
        sa.Column(
            "sequence",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
            comment="Monotonic append order.",
        ),
        sa.Column("entry_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("portfolio_id", sa.String(36), nullable=False),
        sa.Column("occurred_at", _TZ, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(16), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, comment="Canonical JournalDetail JSON."),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.portfolio_id"],
            ondelete="CASCADE",
            name="fk_portfolio_journal_portfolio_id",
        ),
        sa.CheckConstraint(f"kind IN {_KINDS}", name="ck_portfolio_journal_kind"),
        sa.CheckConstraint(f"actor IN {_ACTORS}", name="ck_portfolio_journal_actor"),
        sa.CheckConstraint(f"channel IN {_CHANNELS}", name="ck_portfolio_journal_channel"),
        sa.CheckConstraint("revision > 0", name="ck_portfolio_journal_revision_positive"),
    )
    op.create_index(
        "ix_portfolio_journal_portfolio_sequence",
        "portfolio_journal_entries",
        ["portfolio_id", sa.text("sequence DESC")],
    )


def _create_backtest_jobs() -> None:
    """The async portfolio backtest queue."""
    op.create_table(
        "portfolio_backtest_jobs",
        sa.Column("job_id", sa.UUID(), primary_key=True),
        sa.Column("portfolio_id", sa.String(36), nullable=False),
        sa.Column("portfolio_revision", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "payload", sa.Text(), nullable=False, comment="Resolved PortfolioBacktestPlan JSON."
        ),
        sa.Column("actor", sa.String(16), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("evaluation_start", _TZ, nullable=False),
        sa.Column("evaluation_end", _TZ, nullable=False),
        sa.Column("sleeve_count", sa.Integer(), nullable=False),
        sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(256), nullable=True),
        sa.Column("failed_detail", sa.String(500), nullable=True),
        sa.Column("result_fingerprint", sa.String(71), nullable=True),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
        sa.Column("expires_at", _TZ, nullable=False),
        _portfolio_fk("portfolio_backtest_jobs"),
        sa.CheckConstraint(f"status IN {_STATUSES}", name="ck_portfolio_backtest_jobs_status"),
        sa.CheckConstraint(f"actor IN {_ACTORS}", name="ck_portfolio_backtest_jobs_actor"),
        sa.CheckConstraint(f"channel IN {_CHANNELS}", name="ck_portfolio_backtest_jobs_channel"),
        sa.CheckConstraint(
            "progress_current >= 0 AND progress_total >= 0 AND sleeve_count > 0",
            name="ck_portfolio_backtest_jobs_counts",
        ),
        sa.CheckConstraint(
            f"result_fingerprint IS NULL OR result_fingerprint ~ {_FP}",
            name="ck_portfolio_backtest_jobs_result_format",
        ),
    )
    op.create_index(
        "ix_portfolio_backtest_jobs_status_created",
        "portfolio_backtest_jobs",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_portfolio_backtest_jobs_portfolio_created",
        "portfolio_backtest_jobs",
        ["portfolio_id", sa.text("created_at DESC")],
    )


def _create_backtest_results() -> None:
    """Content-addressed portfolio backtest results."""
    op.create_table(
        "published_portfolio_backtests",
        sa.Column("result_fingerprint", sa.String(71), primary_key=True),
        sa.Column("portfolio_id", sa.String(36), nullable=False),
        sa.Column("portfolio_revision", sa.BigInteger(), nullable=False),
        sa.Column("evaluation_start", _TZ, nullable=False),
        sa.Column("evaluation_end", _TZ, nullable=False),
        sa.Column("listing", sa.Text(), nullable=False, comment="PortfolioBacktestListing JSON."),
        sa.Column("canonical_result", sa.Text(), nullable=False),
        sa.Column("published_at", _TZ, nullable=False),
        _portfolio_fk("published_portfolio_backtests"),
        sa.CheckConstraint(
            f"result_fingerprint ~ {_FP}",
            name="ck_published_portfolio_backtests_fingerprint_format",
        ),
    )
    op.create_index(
        "ix_published_portfolio_backtests_portfolio_published",
        "published_portfolio_backtests",
        ["portfolio_id", sa.text("published_at DESC"), "result_fingerprint"],
    )
