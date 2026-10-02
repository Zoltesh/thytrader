"""Portfolio deployment, portfolio breakers, and manager proposals.

Revision ID: 0056
Revises: 0055
Create Date: 2026-10-02

ADR 0091. Additive only; no existing row changes:

* ``deployments.portfolio_id`` — the deployed portfolio a book is a sleeve of (nullable,
  ``ON DELETE SET NULL`` so a book outlives its portfolio row), set once at creation.
* ``portfolio_runtime`` — one row per deployed portfolio: the current run's start, the
  latched breaker (reason, detail, instant), and the UTC day-open and high-water equity
  baselines the breakers measure against.
* ``portfolio_proposals`` — manager proposals (rebalance, pause/resume a sleeve, add a
  sleeve) with rationale, cited evidence, and the approve/decline/auto-apply outcome.
* ``portfolio_journal_entries.kind`` gains the deployment, breaker, and proposal events.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None

_TZ = sa.DateTime(timezone=True)
_FRACTION = "'^(0|[1-9][0-9]*)([.][0-9]{1,4})?$'"
_SIGNED = "'^-?(0|[1-9][0-9]*)([.][0-9]+)?$'"
_FOUNDATION_KINDS = (
    "'created', 'settings_changed', 'sleeve_added', 'sleeve_updated', 'sleeve_removed', "
    "'weights_changed', 'limits_changed', 'manager_changed', 'backtest_run'"
)
_RUNTIME_KINDS = (
    "'deployment_started', 'deployment_paused', 'deployment_resumed', 'deployment_stopped', "
    "'breaker_tripped', 'breaker_reset', 'proposal_submitted', 'proposal_approved', "
    "'proposal_declined', 'proposal_failed'"
)


def upgrade() -> None:
    """Tag deployments with their portfolio and add runtime state and proposals."""
    op.add_column(
        "deployments",
        sa.Column(
            "portfolio_id",
            sa.String(36),
            nullable=True,
            comment="Deployed portfolio this book is a sleeve of (ADR 0091); set at creation.",
        ),
    )
    op.create_foreign_key(
        "fk_deployments_portfolio_id",
        "deployments",
        "portfolios",
        ["portfolio_id"],
        ["portfolio_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_deployments_portfolio_id",
        "deployments",
        ["portfolio_id"],
        postgresql_where=sa.text("portfolio_id IS NOT NULL"),
    )
    _create_runtime()
    _create_proposals()
    op.drop_constraint("ck_portfolio_journal_kind", "portfolio_journal_entries", type_="check")
    op.create_check_constraint(
        "ck_portfolio_journal_kind",
        "portfolio_journal_entries",
        f"kind IN ({_FOUNDATION_KINDS}, {_RUNTIME_KINDS})",
    )


def downgrade() -> None:
    """Drop the runtime tables and the deployment tag (runtime journal rows go first)."""
    # The kind list is a module constant (no external input), so building it is safe.
    purge = f"DELETE FROM portfolio_journal_entries WHERE kind IN ({_RUNTIME_KINDS})"  # noqa: S608
    op.execute(sa.text(purge))
    op.drop_constraint("ck_portfolio_journal_kind", "portfolio_journal_entries", type_="check")
    op.create_check_constraint(
        "ck_portfolio_journal_kind",
        "portfolio_journal_entries",
        f"kind IN ({_FOUNDATION_KINDS})",
    )
    op.drop_table("portfolio_proposals")
    op.drop_table("portfolio_runtime")
    op.drop_index("ix_deployments_portfolio_id", table_name="deployments")
    op.drop_constraint("fk_deployments_portfolio_id", "deployments", type_="foreignkey")
    op.drop_column("deployments", "portfolio_id")


def _create_runtime() -> None:
    """One runtime row per deployed portfolio (breaker latch and equity baselines)."""
    op.create_table(
        "portfolio_runtime",
        sa.Column("portfolio_id", sa.String(36), primary_key=True),
        sa.Column("run_started_at", _TZ, nullable=True),
        sa.Column("breaker_reason", sa.String(48), nullable=True),
        sa.Column("breaker_detail", sa.String(500), nullable=True),
        sa.Column("breaker_latched_at", _TZ, nullable=True),
        sa.Column("day_open_equity", sa.String(64), nullable=True),
        sa.Column("day_open_at", _TZ, nullable=True),
        sa.Column("high_water_mark_equity", sa.String(64), nullable=True),
        sa.Column("last_equity", sa.String(64), nullable=True),
        sa.Column("last_evaluated_at", _TZ, nullable=True),
        sa.Column(
            "revision", sa.BigInteger(), nullable=False, comment="Compare-and-set write counter."
        ),
        sa.Column("updated_at", _TZ, nullable=False),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.portfolio_id"],
            ondelete="CASCADE",
            name="fk_portfolio_runtime_portfolio_id",
        ),
        sa.CheckConstraint("revision > 0", name="ck_portfolio_runtime_revision_positive"),
        sa.CheckConstraint(
            "breaker_reason IS NULL OR breaker_reason IN "
            "('PORTFOLIO_DAILY_LOSS_STOP', 'PORTFOLIO_DRAWDOWN_STOP')",
            name="ck_portfolio_runtime_breaker_reason",
        ),
        sa.CheckConstraint(
            "(breaker_reason IS NULL) = (breaker_latched_at IS NULL)",
            name="ck_portfolio_runtime_breaker_latch",
        ),
        sa.CheckConstraint(
            f"(day_open_equity IS NULL OR day_open_equity ~ {_SIGNED}) "
            f"AND (high_water_mark_equity IS NULL OR high_water_mark_equity ~ {_SIGNED}) "
            f"AND (last_equity IS NULL OR last_equity ~ {_SIGNED})",
            name="ck_portfolio_runtime_equity_formats",
        ),
    )


def _create_proposals() -> None:
    """Manager proposals and their decisions."""
    op.create_table(
        "portfolio_proposals",
        sa.Column("proposal_id", sa.UUID(), primary_key=True),
        sa.Column("portfolio_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("change", sa.Text(), nullable=False, comment="Canonical ProposalChange JSON."),
        sa.Column("evidence", sa.Text(), nullable=False, comment="Canonical evidence JSON array."),
        sa.Column("base_revision", sa.BigInteger(), nullable=False),
        sa.Column("submitted_by", sa.String(16), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("approval_reason", sa.String(500), nullable=True),
        sa.Column("weight_moved", sa.String(16), nullable=True),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("expires_at", _TZ, nullable=False),
        sa.Column("decided_at", _TZ, nullable=True),
        sa.Column("decided_by", sa.String(16), nullable=True),
        sa.Column("decision_note", sa.String(500), nullable=True),
        sa.Column("auto_applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("applied_revision", sa.BigInteger(), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("failure_message", sa.String(500), nullable=True),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.portfolio_id"],
            ondelete="CASCADE",
            name="fk_portfolio_proposals_portfolio_id",
        ),
        sa.CheckConstraint(
            "kind IN ('rebalance', 'pause_sleeve', 'resume_sleeve', 'add_sleeve')",
            name="ck_portfolio_proposals_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'declined', 'failed', 'expired')",
            name="ck_portfolio_proposals_status",
        ),
        sa.CheckConstraint(
            "submitted_by IN ('manager', 'operator')",
            name="ck_portfolio_proposals_submitted_by",
        ),
        sa.CheckConstraint(
            "decided_by IS NULL OR decided_by IN ('operator', 'manager', 'system')",
            name="ck_portfolio_proposals_decided_by",
        ),
        sa.CheckConstraint(
            f"weight_moved IS NULL OR weight_moved ~ {_FRACTION}",
            name="ck_portfolio_proposals_weight_moved_format",
        ),
        sa.CheckConstraint(
            "base_revision > 0", name="ck_portfolio_proposals_base_revision_positive"
        ),
    )
    op.create_index(
        "ix_portfolio_proposals_portfolio_created",
        "portfolio_proposals",
        ["portfolio_id", sa.text("created_at DESC")],
    )
