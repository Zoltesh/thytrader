"""Add fill-application ledger marker and per-position last-fill intent identity.

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the applied-fill marker and the intent identity behind add-count semantics.

    ``execution_fills.applied_at`` records when a fill's cash/position/order effect
    was durably applied, atomically with the fill's own insert, so a crash between
    "recorded" and "applied" is detectable and repairable instead of silently
    diverging economic state from fill evidence. Existing rows predate the atomic
    ledger and were already applied by the prior (non-atomic) code path, so they
    backfill from ``filled_at`` rather than staying unapplied.

    ``execution_positions.last_fill_intent_id`` tracks the order intent behind the
    most recently applied fragment so ``add_count`` can count distinct filled
    entry/add *intents* instead of raw execution fragments (one order can fill in
    many partial fragments without exhausting the strategy's pyramiding budget).
    """
    op.add_column(
        "execution_fills",
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE execution_fills SET applied_at = filled_at WHERE applied_at IS NULL")
    op.add_column(
        "execution_positions",
        sa.Column("last_fill_intent_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_execution_positions_last_fill_intent_id",
        "execution_positions",
        "order_intents",
        ["last_fill_intent_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Drop the applied-fill marker and the last-fill-intent identity."""
    op.drop_constraint(
        "fk_execution_positions_last_fill_intent_id",
        "execution_positions",
        type_="foreignkey",
    )
    op.drop_column("execution_positions", "last_fill_intent_id")
    op.drop_column("execution_fills", "applied_at")
