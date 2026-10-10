"""Record execution-worker cycle timing (ADR 0131).

Revision ID: 0075
Revises: 0074
Create Date: 2026-10-10

Additive: one new table, no existing row or constraint changes.

* ``execution_cycles``: one row per execution-worker cycle, inserted when the cycle starts
  and completed with its duration and report JSON (phase timings, slowest books, venue
  request counts and latency, deploy-window cache state) when it finishes. The worker keeps
  one day of rows.

Downgrade drops the table; it holds telemetry only, so no trading state is lost.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the execution cycle timing table."""
    op.create_table(
        "execution_cycles",
        sa.Column("cycle_id", sa.UUID(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="UTC instant the execution-worker cycle started.",
        ),
        sa.Column(
            "interval_seconds",
            sa.Integer(),
            nullable=False,
            comment="Configured execution-worker interval in force for this cycle.",
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="UTC completion instant; NULL while the cycle runs or if it never finished.",
        ),
        sa.Column(
            "duration_seconds",
            sa.Float(),
            nullable=True,
            comment="Wall-clock cycle duration; NULL until the cycle completes.",
        ),
        sa.Column(
            "report_json",
            sa.Text(),
            nullable=True,
            comment="Canonical execution cycle report JSON (phases, slowest books, venue calls).",
        ),
        sa.CheckConstraint("interval_seconds >= 1", name="ck_execution_cycles_interval"),
        sa.CheckConstraint(
            "(completed_at IS NULL) = (report_json IS NULL)",
            name="ck_execution_cycles_completion",
        ),
        sa.PrimaryKeyConstraint("cycle_id"),
        comment="Execution-worker cycle timing telemetry, retained for one day (ADR 0131).",
    )
    op.create_index("ix_execution_cycles_started_at", "execution_cycles", ["started_at"])


def downgrade() -> None:
    """Drop the execution cycle timing table (telemetry only)."""
    op.drop_index("ix_execution_cycles_started_at", table_name="execution_cycles")
    op.drop_table("execution_cycles")
