"""Agent desks, their funded books and their journaled wakes (ADR 0147).

Revision ID: 0080
Revises: 0079
Create Date: 2026-10-20

Additive: three new tables, no existing row or constraint changes.

* ``desks``: one row per desk (a paper mandate an external harness wakes on a cadence).
* ``desk_books``: one row per discretionary paper book a desk funded, settled once flat.
* ``desk_wakes``: every wake, including no-trade, invalid and refused answers.

Downgrade drops the tables. The discretionary books themselves stay in ``deployments``.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the desk tables."""
    op.create_table(
        "desks",
        sa.Column("desk_id", sa.String(length=36), nullable=False, comment="UUIDv7 desk identity."),
        sa.Column(
            "revision", sa.BigInteger(), nullable=False, comment="Optimistic revision, starts at 1."
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "desk_json", sa.Text(), nullable=False, comment="Canonical desk JSON (ADR 0147)."
        ),
        sa.CheckConstraint("revision > 0", name="ck_desks_revision_positive"),
        sa.PrimaryKeyConstraint("desk_id"),
        comment="Agent desks: a paper mandate an external harness wakes on a cadence.",
    )
    op.create_table(
        "desk_books",
        sa.Column(
            "deployment_id",
            sa.UUID(),
            nullable=False,
            comment="The discretionary paper book the desk funded.",
        ),
        sa.Column("desk_id", sa.String(length=36), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("book_json", sa.Text(), nullable=False, comment="Canonical desk book JSON."),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["desk_id"], ["desks.desk_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("deployment_id"),
        comment="One row per trade a desk funded; settled when the book is flat and stopped.",
    )
    op.create_index("ix_desk_books_desk_opened", "desk_books", ["desk_id", "opened_at"])
    op.create_table(
        "desk_wakes",
        sa.Column("wake_id", sa.String(length=36), nullable=False, comment="UUIDv7 wake identity."),
        sa.Column("desk_id", sa.String(length=36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "outcome",
            sa.String(length=16),
            nullable=False,
            comment="applied, refused, invalid or error.",
        ),
        sa.Column("wake_json", sa.Text(), nullable=False, comment="Canonical wake JSON."),
        sa.ForeignKeyConstraint(["desk_id"], ["desks.desk_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("wake_id"),
        comment="Every desk wake: what the model answered and what ThyTrader did with it.",
    )
    op.create_index("ix_desk_wakes_desk_started", "desk_wakes", ["desk_id", "started_at"])


def downgrade() -> None:
    """Drop the desk tables (journal and desk metadata only)."""
    op.drop_index("ix_desk_wakes_desk_started", table_name="desk_wakes")
    op.drop_table("desk_wakes")
    op.drop_index("ix_desk_books_desk_opened", table_name="desk_books")
    op.drop_table("desk_books")
    op.drop_table("desks")
