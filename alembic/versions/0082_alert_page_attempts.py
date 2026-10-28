"""Count alert delivery attempts per severity, so each severity is its own page (ADR 0115).

Revision ID: 0082
Revises: 0081
Create Date: 2026-10-28

Additive: three integer columns defaulting to zero, no constraint changes.

* ``operator_alerts.info_attempts`` / ``warning_attempts`` / ``critical_attempts``: delivery
  attempts claimed at each severity. A page at one severity spends only its own budget, so a
  lower page that exhausted its attempts, or a downgrade flap, never starves an escalation.
  ``delivery_attempts`` stays the row's total.

Backfill: a sent row's existing attempts are charged to its covered severity. A row never sent
has unknown attempt severities and starts with zero, so an open unsent row gets one fresh budget
after the upgrade: a possible duplicate, never a missed page.

Downgrade drops the three columns.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None

_LEVELS = ("info", "warning", "critical")


def upgrade() -> None:
    """Add the per-severity counters and charge sent rows' attempts to their coverage."""
    for level in _LEVELS:
        op.add_column(
            "operator_alerts",
            sa.Column(f"{level}_attempts", sa.Integer(), nullable=False, server_default="0"),
        )
        op.execute(
            sa.text(
                f"UPDATE operator_alerts SET {level}_attempts = delivery_attempts "  # noqa: S608
                "WHERE covered_severity = :level"
            ).bindparams(level=level)
        )


def downgrade() -> None:
    """Drop the per-severity counters."""
    for level in reversed(_LEVELS):
        op.drop_column("operator_alerts", f"{level}_attempts")
