"""Paper futures book tables (ADR 0129 §4, Alembic 0073).

Decimal amounts are exact strings; ``NULL`` is unknown, never zero. Column notes are Python
comments: the creating migration sets no database comments.
"""

from __future__ import annotations

from sqlalchemy import (
    UUID,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKeyConstraint,
    String,
    Table,
)

from thytrader.persistence.schema_metadata import metadata

_AMOUNT = String(64)

deployment_instrument_contracts = Table(
    "deployment_instrument_contracts",
    metadata,
    Column("deployment_id", UUID(), primary_key=True),
    Column("product_id", String(32), nullable=False),
    Column("kind", String(24), nullable=False),
    Column("underlying", String(32), nullable=False),
    Column("contract_size", _AMOUNT, nullable=False),
    Column("settlement_currency", String(8), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),  # NULL for perps.
    Column("listed_expiry", Date(), nullable=False),
    # Payload fingerprint of the catalog observation bound at start.
    Column("catalog_fingerprint", String(71), nullable=False),
    Column("fee_per_contract", _AMOUNT, nullable=False),  # USD per contract.
    Column("bound_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
    CheckConstraint(
        "kind IN ('dated_future', 'perpetual_future')",
        name="ck_deployment_instrument_contracts_kind",
    ),
    CheckConstraint(
        "settlement_currency = 'USD'",
        name="ck_deployment_instrument_contracts_settlement",
    ),
)

futures_funding_entries = Table(
    "futures_funding_entries",
    metadata,
    Column("deployment_id", UUID(), primary_key=True),
    Column("product_id", String(32), primary_key=True),
    Column("funding_time", DateTime(timezone=True), primary_key=True),
    # Signed base quantity held at the funding hour (negative for shorts).
    Column("signed_quantity", _AMOUNT, nullable=False),
    Column("mark_price", _AMOUNT, nullable=False),
    Column("rate", _AMOUNT, nullable=False),  # Settled hourly rate.
    # Cash change applied to the book: -signed_quantity x mark_price x rate (USD).
    Column("amount", _AMOUNT, nullable=False),
    Column("applied_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
)
