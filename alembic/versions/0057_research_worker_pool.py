"""Lease research jobs to the research worker pool and record worker slots.

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-02

ADR 0092. Research compute (backtests, studies, portfolio backtests) moves out of the
API into the ``research-worker`` service. Forward-safe, additive changes:

* ``research_jobs`` and ``portfolio_backtest_jobs`` gain ``lease_owner``,
  ``lease_expires_at`` (both nullable) and ``attempts`` (default 0). A worker claims a
  queued row with ``FOR UPDATE SKIP LOCKED`` and keeps renewing the lease; a running row
  whose lease expired (or that has none, such as a row the pre-0057 API was running) is
  re-queued, or failed once ``attempts`` reaches the configured maximum.
* ``research_jobs.error_code`` (nullable) names why a job failed, so a synchronous submit
  answers with the same HTTP status it did when research ran inline.
* ``research_workers`` holds one self-reported row per worker slot (pid, state, current
  job, jobs completed, RSS, heartbeat) for operator health.

Downgrade drops them; rows simply lose their lease (the pre-0057 API requeues every
running row at startup).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

_LEASE_COMMENT = "Research worker token holding a running job (ADR 0092)."


def upgrade() -> None:
    """Add lease columns to both job tables, the error code, and the worker slot table."""
    for table in ("research_jobs", "portfolio_backtest_jobs"):
        op.add_column(
            table,
            sa.Column("lease_owner", sa.String(length=96), nullable=True, comment=_LEASE_COMMENT),
        )
        op.add_column(table, sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
        op.add_column(
            table,
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        )
        op.create_check_constraint(f"ck_{table}_attempts", table, "attempts >= 0")
    op.add_column(
        "research_jobs",
        sa.Column(
            "error_code",
            sa.String(length=32),
            nullable=True,
            comment="ResearchJobErrorCode of a failed job (ADR 0092).",
        ),
    )
    op.create_table(
        "research_workers",
        sa.Column(
            "slot",
            sa.Integer(),
            primary_key=True,
            autoincrement=False,
            comment="Supervisor slot index.",
        ),
        sa.Column(
            "pool_size", sa.Integer(), nullable=False, comment="Configured research workers."
        ),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.Column("job_kind", sa.String(length=32), nullable=True),
        sa.Column("jobs_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "rss_bytes",
            sa.BigInteger(),
            nullable=True,
            comment="Resident set size of the process.",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "slot >= 0 AND pool_size >= 1 AND pid >= 1", name="ck_research_workers_ids"
        ),
        sa.CheckConstraint(
            "state IN ('starting', 'idle', 'running', 'stopping')",
            name="ck_research_workers_state",
        ),
        sa.CheckConstraint(
            "job_kind IS NULL OR job_kind IN ('backtest', 'study', 'portfolio_backtest')",
            name="ck_research_workers_job_kind",
        ),
        sa.CheckConstraint(
            "jobs_completed >= 0 AND (rss_bytes IS NULL OR rss_bytes >= 0)",
            name="ck_research_workers_counts",
        ),
        comment="Latest self-report of each research worker process (ADR 0092).",
    )


def downgrade() -> None:
    """Drop the worker slot table, the error code, and both tables' lease columns."""
    op.drop_table("research_workers")
    op.drop_column("research_jobs", "error_code")
    for table in ("portfolio_backtest_jobs", "research_jobs"):
        op.drop_constraint(f"ck_{table}_attempts", table, type_="check")
        op.drop_column(table, "attempts")
        op.drop_column(table, "lease_expires_at")
        op.drop_column(table, "lease_owner")
