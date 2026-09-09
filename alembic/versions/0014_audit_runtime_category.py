"""Allow runtime-control audit events on the existing audit_events category check.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-09
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

_OLD = (
    "category IN ('connection', 'snapshot', 'worker_error', 'market_data', 'websocket', 'research')"
)
_NEW = (
    "category IN ("
    "'connection', 'snapshot', 'worker_error', 'market_data', 'websocket', 'research', 'runtime'"
    ")"
)


def upgrade() -> None:
    """Permit runtime-control audit events without rewriting history."""
    op.drop_constraint("ck_audit_events_category", "audit_events", type_="check")
    op.create_check_constraint("ck_audit_events_category", "audit_events", _NEW)


def downgrade() -> None:
    """Restore the pre-runtime audit category constraint."""
    op.drop_constraint("ck_audit_events_category", "audit_events", type_="check")
    op.create_check_constraint("ck_audit_events_category", "audit_events", _OLD)
