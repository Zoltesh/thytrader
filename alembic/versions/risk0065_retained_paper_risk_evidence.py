"""Retain stopped paper risk evidence when the strategy root is deleted.

Revision ID: risk0065
Revises: 0064
ADR 0111. Only the identity constraint changes; no financial history is rewritten.
"""

import sqlalchemy as sa

from alembic import op

revision = "risk0065"
down_revision = "0064"
branch_labels = None
depends_on = None

_IDENTITY = "ck_deployments_kind_identity"


def upgrade() -> None:
    """Allow detached stopped strategy books in paper as well as live mode."""
    op.drop_constraint(_IDENTITY, "deployments", type_="check")
    op.create_check_constraint(
        _IDENTITY,
        "deployments",
        "(kind = 'strategy' AND strategy_fingerprint IS NOT NULL AND ("
        "strategy_id IS NOT NULL OR status = 'stopped')) OR "
        "(kind = 'discretionary' AND strategy_fingerprint IS NULL AND strategy_id IS NULL "
        "AND timeframe IN ('1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d'))",
    )


def downgrade() -> None:
    """Refuse to destroy detached paper ledgers just to restore the old constraint."""
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM deployments WHERE kind = 'strategy' AND mode = 'paper' "
                "AND strategy_id IS NULL"
            )
        )
        .scalar_one()
    )
    if count:
        raise RuntimeError("Cannot downgrade risk0065 while detached paper risk evidence exists.")
    op.drop_constraint(_IDENTITY, "deployments", type_="check")
    op.create_check_constraint(
        _IDENTITY,
        "deployments",
        "(kind = 'strategy' AND strategy_fingerprint IS NOT NULL AND ("
        "strategy_id IS NOT NULL OR (mode = 'live' AND status = 'stopped'))) OR "
        "(kind = 'discretionary' AND strategy_fingerprint IS NULL AND strategy_id IS NULL "
        "AND timeframe IN ('1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d'))",
    )
