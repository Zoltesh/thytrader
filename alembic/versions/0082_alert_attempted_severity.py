"""Record the highest severity an alert delivery attempt was claimed for (ADR 0115).

Revision ID: 0082
Revises: 0081
Create Date: 2026-10-28

Additive: one nullable column, no existing row or constraint changes.

* ``operator_alerts.attempted_severity``: the highest severity a delivery attempt was claimed
  for. A page at a higher severity (an escalation in place, including after a lower page
  exhausted its attempts or was acknowledged ambiguously) is a new page with its own attempt
  budget; ``covered_severity`` still records what was actually sent. Rows from before this
  revision have none: their budget is kept until they escalate above their covered severity.

Downgrade drops it.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the attempted severity."""
    op.add_column(
        "operator_alerts",
        sa.Column("attempted_severity", sa.String(length=12), nullable=True),
    )


def downgrade() -> None:
    """Drop the column."""
    op.drop_column("operator_alerts", "attempted_severity")
