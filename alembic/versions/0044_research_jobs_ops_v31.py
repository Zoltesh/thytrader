"""Durable research jobs and study plan dedupe.

Revision ID: 0044
Revises: 0041
Create Date: 2026-09-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add durable research jobs and plan-fingerprint dedupe for composed studies."""
    op.create_table(
        "research_jobs",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(length=256), nullable=True),
        sa.Column("run_fingerprint", sa.String(length=71), nullable=True),
        sa.Column("result_fingerprint", sa.String(length=71), nullable=True),
        sa.Column("study_fingerprint", sa.String(length=71), nullable=True),
        sa.Column("plan_fingerprint", sa.String(length=71), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default="false"),
        sa.CheckConstraint("kind IN ('backtest', 'study')", name="ck_research_jobs_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'expired')",
            name="ck_research_jobs_status",
        ),
        sa.CheckConstraint("progress_current >= 0", name="ck_research_jobs_progress_current"),
        sa.CheckConstraint("progress_total >= 0", name="ck_research_jobs_progress_total"),
    )
    op.create_index(
        "ix_research_jobs_status_created",
        "research_jobs",
        ["status", "created_at"],
        unique=False,
    )
    op.add_column(
        "published_research_studies",
        sa.Column("plan_fingerprint", sa.String(length=71), nullable=True),
    )
    op.execute(
        "UPDATE published_research_studies "
        "SET plan_fingerprint = request_fingerprint "
        "WHERE plan_fingerprint IS NULL"
    )
    op.alter_column("published_research_studies", "plan_fingerprint", nullable=False)
    op.create_check_constraint(
        "ck_research_study_plan_fingerprint_format",
        "published_research_studies",
        "plan_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
    )
    op.create_index(
        "ix_published_research_studies_plan_fingerprint",
        "published_research_studies",
        ["plan_fingerprint"],
        unique=True,
    )


def downgrade() -> None:
    """Drop durable research jobs and plan-fingerprint dedupe."""
    op.drop_index(
        "ix_published_research_studies_plan_fingerprint",
        table_name="published_research_studies",
    )
    op.drop_constraint(
        "ck_research_study_plan_fingerprint_format",
        "published_research_studies",
        type_="check",
    )
    op.drop_column("published_research_studies", "plan_fingerprint")
    op.drop_index("ix_research_jobs_status_created", table_name="research_jobs")
    op.drop_table("research_jobs")
