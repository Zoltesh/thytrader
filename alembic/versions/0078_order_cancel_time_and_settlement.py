"""Record each order's first cancel time and its settlement watermark.

Revision ID: 0078
Revises: 0077
ADR 0141. ``canceled_at`` is when an order was first saved canceled (the cancel-rate breaker
counts it); ``settled_at`` ends venue reads of a canceled order after a consistent terminal
read. The real cancel time of orders canceled before this revision is unknown, so they get 61
seconds before the upgrade: just outside every one-minute cancel-rate window, so never counted
as a new cancellation, and still read for 13 minutes 59 seconds after the upgrade before they
may settle, far longer than the venue takes to publish an order's fills.
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
            "UPDATE execution_orders SET canceled_at = now() - interval '61 seconds' "
            "WHERE status = 'canceled'"
        )
    )


def downgrade() -> None:
    """Drop both timestamps; settled orders are simply read again."""
    op.drop_column("execution_orders", "settled_at")
    op.drop_column("execution_orders", "canceled_at")
