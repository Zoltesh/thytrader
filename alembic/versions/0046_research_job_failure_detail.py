"""Structured research-job failure detail.

Revision ID: 0046
Revises: 0045
Create Date: 2026-09-19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add failure phase and underlying-cause detail to research job rows."""
    op.add_column(
        "research_jobs",
        sa.Column("failed_phase", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "research_jobs",
        sa.Column("failed_detail", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    """Drop structured research-job failure detail."""
    op.drop_column("research_jobs", "failed_detail")
    op.drop_column("research_jobs", "failed_phase")
