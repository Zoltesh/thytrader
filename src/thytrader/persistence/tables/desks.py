"""Agent desk tables: desks, their funded books and their journaled wakes (ADR 0147).

Each row keeps the validated model as canonical JSON next to the columns used for lookup.
"""

from __future__ import annotations

from sqlalchemy import (
    UUID,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
)

from thytrader.persistence.schema_metadata import metadata

desks = Table(
    "desks",
    metadata,
    Column("desk_id", String(36), primary_key=True, comment="UUIDv7 desk identity."),
    Column("revision", BigInteger(), nullable=False, comment="Optimistic revision, starts at 1."),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("desk_json", Text(), nullable=False, comment="Canonical desk JSON (ADR 0147)."),
    CheckConstraint("revision > 0", name="ck_desks_revision_positive"),
    comment="Agent desks: a paper mandate an external harness wakes on a cadence.",
)

desk_books = Table(
    "desk_books",
    metadata,
    Column(
        "deployment_id",
        UUID(),
        ForeignKey("deployments.id", ondelete="CASCADE"),
        primary_key=True,
        comment="The discretionary paper book the desk funded.",
    ),
    Column(
        "desk_id",
        String(36),
        ForeignKey("desks.desk_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("opened_at", DateTime(timezone=True), nullable=False),
    Column("settled_at", DateTime(timezone=True), nullable=True),
    Column("book_json", Text(), nullable=False, comment="Canonical desk book JSON."),
    Index("ix_desk_books_desk_opened", "desk_id", "opened_at"),
    comment="One row per trade a desk funded; settled when the book is flat and stopped.",
)

desk_wakes = Table(
    "desk_wakes",
    metadata,
    Column("wake_id", String(36), primary_key=True, comment="UUIDv7 wake identity."),
    Column(
        "desk_id",
        String(36),
        ForeignKey("desks.desk_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("outcome", String(16), nullable=False, comment="applied, refused, invalid or error."),
    Column("wake_json", Text(), nullable=False, comment="Canonical wake JSON."),
    Index("ix_desk_wakes_desk_started", "desk_id", "started_at"),
    comment="Every desk wake: what the model answered and what ThyTrader did with it.",
)

__all__ = ["desk_books", "desk_wakes", "desks"]
