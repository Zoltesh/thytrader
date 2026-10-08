"""Append-only exchange portfolio snapshot history tables."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Numeric,
    String,
    Table,
)

from thytrader.persistence.schema_metadata import metadata

portfolio_snapshots = Table(
    "portfolio_snapshots",
    metadata,
    Column(
        "id",
        BigInteger(),
        primary_key=True,
        autoincrement=True,
        comment="Monotonic surrogate key for append-only snapshots.",
    ),
    Column("as_of", DateTime(timezone=True), nullable=False, comment="Exchange snapshot instant."),
    Column("provider", String(32), nullable=False, comment="Exchange provider identifier."),
    Column(
        "connection_status",
        String(16),
        nullable=False,
        comment="Connection status at snapshot time.",
    ),
    Column("demo", Boolean(), nullable=False, comment="Whether the snapshot used demo data."),
    Column(
        "total_usd_value",
        Numeric(38, 18),
        nullable=False,
        comment="Exact total USD valuation as a decimal.",
    ),
    # Deliberately untyped (NullType): the column is PostgreSQL ``json`` (migration 0001),
    # and PostgresPortfolioHistoryStore.record writes a pre-serialized ``json.dumps``
    # string. Declaring ``JSON()`` here would make SQLAlchemy serialize it a second time
    # and store a JSON string literal instead of an object. Nothing reads this column.
    Column(
        "snapshot",
        nullable=False,
        comment="Complete JSON snapshot preserving all decimal strings.",
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
        comment="Database row insertion timestamp.",
    ),
)

Index(
    "ix_portfolio_snapshots_as_of_desc",
    portfolio_snapshots.c.as_of.desc(),
    portfolio_snapshots.c.id.desc(),
)

__all__ = [
    "portfolio_snapshots",
]
