"""Allow research audit events on the existing audit_events category check.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-08
"""

from __future__ import annotations

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

_OLD = "category IN ('connection', 'snapshot', 'worker_error', 'market_data', 'websocket')"
_NEW = (
    "category IN ('connection', 'snapshot', 'worker_error', 'market_data', 'websocket', 'research')"
)


def upgrade() -> None:
    """Permit research-mutation audit events without rewriting history."""
    op.drop_constraint("ck_audit_events_category", "audit_events", type_="check")
    op.create_check_constraint("ck_audit_events_category", "audit_events", _NEW)


def downgrade() -> None:
    """Restore the pre-research audit category constraint."""
    op.drop_constraint("ck_audit_events_category", "audit_events", type_="check")
    op.create_check_constraint("ck_audit_events_category", "audit_events", _OLD)
