"""Record the severity an alert page covered, and index one alert's history (ADR 0115).

Revision ID: 0081
Revises: 0080
Create Date: 2026-10-25

Additive: one nullable column and one index, no existing row or constraint changes.

* ``operator_alerts.covered_severity``: the severity actually sent to the notifier, or the
  severity of the page a continuation skip relied on. The row's ``severity`` keeps tracking the
  observation, so an escalation past the page can be paged and a downgrade never re-pages.
* ``ix_operator_alerts_history``: ``(code, subject, resolved_at)`` for the entry-block episode
  walk over one alert's resolved rows.

Downgrade drops both.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the covered severity and the per-alert history index."""
    op.add_column(
        "operator_alerts",
        sa.Column("covered_severity", sa.String(length=12), nullable=True),
    )
    op.create_index(
        "ix_operator_alerts_history",
        "operator_alerts",
        ["code", "subject", "resolved_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the index and the column."""
    op.drop_index("ix_operator_alerts_history", table_name="operator_alerts")
    op.drop_column("operator_alerts", "covered_severity")
