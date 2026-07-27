"""Create portfolio_snapshots table.

Revision ID: 0001
Revises:
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the append-only portfolio_snapshots table and its history index."""
    op.create_table(
        "portfolio_snapshots",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
            comment="Monotonic surrogate key for append-only snapshots.",
        ),
        sa.Column(
            "as_of",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Exchange snapshot instant.",
        ),
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            comment="Exchange provider identifier.",
        ),
        sa.Column(
            "connection_status",
            sa.String(length=16),
            nullable=False,
            comment="Connection status at snapshot time.",
        ),
        sa.Column(
            "demo",
            sa.Boolean(),
            nullable=False,
            comment="Whether the snapshot used demo data.",
        ),
        sa.Column(
            "total_usd_value",
            sa.Numeric(precision=38, scale=18),
            nullable=False,
            comment="Exact total USD valuation as a decimal.",
        ),
        sa.Column(
            "snapshot",
            sa.JSON(),
            nullable=False,
            comment="Complete JSON snapshot preserving all decimal strings.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Database row insertion timestamp.",
        ),
    )
    op.create_index(
        "ix_portfolio_snapshots_as_of_desc",
        "portfolio_snapshots",
        [sa.text("as_of DESC"), sa.text("id DESC")],
    )


def downgrade() -> None:
    """Drop the portfolio_snapshots table and its history index."""
    op.drop_index("ix_portfolio_snapshots_as_of_desc", table_name="portfolio_snapshots")
    op.drop_table("portfolio_snapshots")
