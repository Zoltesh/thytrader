"""Add discretionary deployments and intent origin/idempotency.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

_KIND_IDENTITY = (
    "("
    "kind = 'strategy' AND strategy_fingerprint IS NOT NULL AND strategy_id IS NOT NULL"
    ") OR ("
    "kind = 'discretionary' AND strategy_fingerprint IS NULL AND strategy_id IS NULL "
    "AND timeframe IN ('1h', '5m')"
    ")"
)


def upgrade() -> None:
    """Allow discretionary books and origin-attributed idempotent intents."""
    op.add_column(
        "deployments",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="strategy"),
    )
    op.add_column(
        "deployments",
        sa.Column("timeframe", sa.String(length=8), nullable=True),
    )
    op.alter_column(
        "deployments", "strategy_fingerprint", existing_type=sa.String(71), nullable=True
    )
    op.alter_column("deployments", "strategy_id", existing_type=sa.String(36), nullable=True)
    op.create_check_constraint(
        "ck_deployments_kind",
        "deployments",
        "kind IN ('strategy', 'discretionary')",
    )
    op.create_check_constraint(
        "ck_deployments_timeframe",
        "deployments",
        "timeframe IS NULL OR timeframe IN ('1h', '5m')",
    )
    op.create_check_constraint("ck_deployments_kind_identity", "deployments", _KIND_IDENTITY)
    op.add_column(
        "order_intents",
        sa.Column("origin", sa.String(length=8), nullable=False, server_default="runtime"),
    )
    op.add_column(
        "order_intents",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.create_check_constraint(
        "ck_order_intents_origin",
        "order_intents",
        "origin IN ('human', 'agent', 'runtime')",
    )
    op.create_unique_constraint(
        "ux_order_intents_idempotency_key", "order_intents", ["idempotency_key"]
    )


def downgrade() -> None:
    """Restore strategy-only deployments and unattributed intents."""
    op.drop_constraint("ux_order_intents_idempotency_key", "order_intents", type_="unique")
    op.drop_constraint("ck_order_intents_origin", "order_intents", type_="check")
    op.drop_column("order_intents", "idempotency_key")
    op.drop_column("order_intents", "origin")
    op.drop_constraint("ck_deployments_kind_identity", "deployments", type_="check")
    op.drop_constraint("ck_deployments_timeframe", "deployments", type_="check")
    op.drop_constraint("ck_deployments_kind", "deployments", type_="check")
    op.alter_column("deployments", "strategy_id", existing_type=sa.String(36), nullable=False)
    op.alter_column(
        "deployments",
        "strategy_fingerprint",
        existing_type=sa.String(71),
        nullable=False,
    )
    op.drop_column("deployments", "timeframe")
    op.drop_column("deployments", "kind")
