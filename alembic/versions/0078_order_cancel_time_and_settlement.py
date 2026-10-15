"""Record each order's first cancel time and its settlement watermark.

Revision ID: 0078
Revises: 0077
ADR 0141. ``canceled_at`` is when an order was first saved canceled (the cancel-rate breaker
counts it); ``settled_at`` ends venue reads of a canceled order after a consistent terminal
read. Orders canceled before this revision get their last save time: no earlier than the real
cancel, so settlement still waits the full delay after it, and a cancel the old breaker counted by
that time is counted the same way for at most a minute. That covers canceled orders and those a
failed read left UNKNOWN after the venue had reported them canceled (``terminal_read_unconfirmed``),
so their first good read after the upgrade is not a new cancellation. Only the application's own
timestamps are used: the database clock is never mixed in.
"""

import sqlalchemy as sa

from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add both timestamps and backfill the cancel time of existing canceled orders."""
    op.add_column(
        "execution_orders",
        sa.Column(
            "canceled_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="First time the order was saved canceled; never cleared (ADR 0141).",
        ),
    )
    op.add_column(
        "execution_orders",
        sa.Column(
            "settled_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "A consistent terminal read ended venue reads of the canceled order (ADR 0141)."
            ),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE execution_orders SET canceled_at = updated_at WHERE status = 'canceled' "
            "OR (status = 'unknown' AND reject_reason = 'terminal_read_unconfirmed')"
        )
    )


def downgrade() -> None:
    """Drop both timestamps; settled orders are simply read again."""
    op.drop_column("execution_orders", "settled_at")
    op.drop_column("execution_orders", "canceled_at")
