"""Record ADR 0050 breaker overlay without rewriting stored policy JSON.

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-16
"""

from __future__ import annotations

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_OVERLAY_COMMENT = (
    "thytrader-risk-policy-v1; omitted breaker keys overlay compiled defaults "
    "at load (ADR 0050). Stored canonical JSON and fingerprints stay unchanged."
)


def upgrade() -> None:
    """Stamp breaker overlay as application-level; do not rewrite published JSON."""
    op.execute(f"COMMENT ON TABLE published_risk_policies IS '{_OVERLAY_COMMENT}'")


def downgrade() -> None:
    """Drop the ADR 0050 overlay comment; stored canonical JSON is unchanged."""
    op.execute("COMMENT ON TABLE published_risk_policies IS NULL")
