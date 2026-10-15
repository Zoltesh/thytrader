"""Record the evidence each live start was assessed on (ADR 0143).

Revision ID: 0079
Revises: 0078
Create Date: 2026-10-15

Additive: one new table, no existing row or constraint changes.

* ``live_start_evidence``: one row per live deployment, written when it starts, holding what
  its exact strategy fingerprint had shown (out-of-sample study windows, paper books) and
  why that was thin, if it was. The first record of a deployment is kept.

Downgrade drops the table; it holds evidence only, so no trading state is lost.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the live-start evidence table."""
    op.create_table(
        "live_start_evidence",
        sa.Column("deployment_id", sa.UUID(), nullable=False),
        sa.Column(
            "strategy_fingerprint",
            sa.String(length=71),
            nullable=False,
            comment="The started snapshot's fingerprint the evidence was gathered for.",
        ),
        sa.Column(
            "assessed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="UTC instant the live start's evidence was assessed.",
        ),
        sa.Column(
            "evidence_json",
            sa.Text(),
            nullable=False,
            comment="Canonical live-start evidence JSON (thytrader-live-start-evidence-v1).",
        ),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("deployment_id"),
        comment="What a strategy had shown when each live bot started; first record kept.",
    )


def downgrade() -> None:
    """Drop the live-start evidence table (evidence only)."""
    op.drop_table("live_start_evidence")
