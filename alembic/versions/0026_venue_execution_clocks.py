"""Widen discretionary and strategy deployment clocks to ingested venue TFs.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-16
"""

from __future__ import annotations

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

_OLD_CLOCKS = "timeframe IN ('1h', '5m')"
_NEW_CLOCKS = "timeframe IN ('1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d')"
_KIND_IDENTITY_PREFIX = (
    "("
    "kind = 'strategy' AND strategy_fingerprint IS NOT NULL AND strategy_id IS NOT NULL"
    ") OR ("
    "kind = 'discretionary' AND strategy_fingerprint IS NULL AND strategy_id IS NULL "
    "AND "
)


def upgrade() -> None:
    """Permit ingested venue clocks on stored deployment timeframes."""
    op.drop_constraint("ck_deployments_timeframe", "deployments", type_="check")
    op.create_check_constraint(
        "ck_deployments_timeframe",
        "deployments",
        f"timeframe IS NULL OR {_NEW_CLOCKS}",
    )
    op.drop_constraint("ck_deployments_kind_identity", "deployments", type_="check")
    op.create_check_constraint(
        "ck_deployments_kind_identity",
        "deployments",
        f"{_KIND_IDENTITY_PREFIX}{_NEW_CLOCKS})",
    )


def downgrade() -> None:
    """Restore 1h/5m deployment clocks. Rows with other timeframes must be removed first."""
    op.drop_constraint("ck_deployments_kind_identity", "deployments", type_="check")
    op.create_check_constraint(
        "ck_deployments_kind_identity",
        "deployments",
        f"{_KIND_IDENTITY_PREFIX}{_OLD_CLOCKS})",
    )
    op.drop_constraint("ck_deployments_timeframe", "deployments", type_="check")
    op.create_check_constraint(
        "ck_deployments_timeframe",
        "deployments",
        f"timeframe IS NULL OR {_OLD_CLOCKS}",
    )
