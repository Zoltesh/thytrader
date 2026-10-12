"""Persist independent futures safety obligations (ADR 0135).

Revision ID: 0076
Revises: 0075

Additive defaults leave spot and paper unchanged. Legacy injected-runtime markers are
conservatively retained, never converted into venue-observation provenance.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add typed durable obligations to the revision-fenced deployment row."""
    op.add_column(
        "deployments",
        sa.Column("futures_derisk_pending", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "deployments",
        sa.Column("futures_flat_confirmation_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("""
        UPDATE deployments SET futures_derisk_pending = true
        WHERE mode = 'live' AND mismatch_detail LIKE 'FUTURES_DERISK_PENDING%'
    """)
    op.execute("""
        UPDATE deployments SET futures_flat_confirmation_after = updated_at
        WHERE mode = 'live' AND (
            futures_derisk_pending
            OR mismatch_detail LIKE 'FUTURES_FLAT_CONFIRMATION_PENDING%'
            OR (product_id LIKE '%-CDE' AND lifecycle_command = 'flatten')
        )
    """)


def downgrade() -> None:
    """Refuse to discard outstanding financial workflow state."""
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM deployments WHERE futures_derisk_pending
                       OR futures_flat_confirmation_after IS NOT NULL) THEN
                RAISE EXCEPTION 'Cannot discard pending futures safety obligations';
            END IF;
        END $$
    """)
    op.drop_column("deployments", "futures_flat_confirmation_after")
    op.drop_column("deployments", "futures_derisk_pending")
