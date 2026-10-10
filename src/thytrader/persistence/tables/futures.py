"""Coinbase CFM futures observation tables (ADR 0126, Alembic 0071).

Decimal amounts and rates are exact strings (``String(64)``), like every other venue
amount in this schema. Column notes are Python comments: the creating migration sets no
database comments and ``alembic check`` compares them.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Table,
)

from thytrader.persistence.schema_metadata import metadata

futures_catalog_poll_state = Table(
    "futures_catalog_poll_state",
    metadata,
    Column("provider", String(32), primary_key=True),
    Column("last_attempt_at", DateTime(timezone=True), nullable=False),
    Column("last_success_at", DateTime(timezone=True), nullable=True),
    Column("consecutive_failures", Integer(), nullable=False, server_default="0"),
    Column("failure_code", String(64), nullable=True),
    # Fingerprint of the last complete listing (futures catalog fingerprint).
    Column("listing_fingerprint", String(71), nullable=True),
    Column("contract_count", Integer(), nullable=True),
    Column("perpetual_count", Integer(), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

futures_instrument_observations = Table(
    "futures_instrument_observations",
    metadata,
    Column("product_id", String(32), primary_key=True),
    Column("first_seen_at", DateTime(timezone=True), primary_key=True),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("payload_fingerprint", String(71), nullable=False),
    Column("kind", String(24), nullable=False),
    Column("contract_code", String(16), nullable=False),
    # contract_root_unit, never derived from the id prefix.
    Column("underlying", String(32), nullable=False),
    Column("settlement_currency", String(8), nullable=False),
    Column("contract_size", String(64), nullable=False),
    Column("price_increment", String(64), nullable=False),
    Column("base_increment", String(64), nullable=False),  # In contracts.
    Column("base_min_size", String(64), nullable=False),  # In contracts.
    # Raw venue expiry; perps report a far-future sentinel (2089-12-30).
    Column("venue_expiry_at", DateTime(timezone=True), nullable=False),
    Column("listed_expiry", Date(), nullable=False),  # The day encoded in the id.
    Column("twenty_four_by_seven", Boolean(), nullable=False),
    # Margin fractions; NULL means the venue did not list them (unknown, never zero).
    Column("intraday_long_margin_rate", String(64), nullable=True),
    Column("intraday_short_margin_rate", String(64), nullable=True),
    Column("overnight_long_margin_rate", String(64), nullable=True),
    Column("overnight_short_margin_rate", String(64), nullable=True),
    Column("funding_interval_seconds", Integer(), nullable=True),  # NULL for dated contracts.
    Column("session_state", String(64), nullable=True),
    Column("maintenance_starts_at", DateTime(timezone=True), nullable=True),
    Column("maintenance_ends_at", DateTime(timezone=True), nullable=True),
    Column("asset_type", String(64), nullable=True),
    Column("trading_enabled", Boolean(), nullable=False),
    CheckConstraint(
        "kind IN ('dated_future', 'perpetual_future')",
        name="ck_futures_instrument_observations_kind",
    ),
    CheckConstraint(
        "last_seen_at >= first_seen_at",
        name="ck_futures_instrument_observations_seen_order",
    ),
)

futures_funding_rates = Table(
    "futures_funding_rates",
    metadata,
    Column("product_id", String(32), primary_key=True),
    Column("funding_time", DateTime(timezone=True), primary_key=True),
    # Settled rate once settled; until then the latest value listed for this hour.
    Column("rate", String(64), nullable=False),
    Column("interval_seconds", Integer(), nullable=False),
    Column("first_observed_at", DateTime(timezone=True), nullable=False),
    Column("last_observed_at", DateTime(timezone=True), nullable=False),
    Column("observation_count", Integer(), nullable=False),
    # Times the listed value changed while the hour was still current.
    Column("revision_count", Integer(), nullable=False, server_default="0"),
    # True once the listing named a later hour for this contract; immutable after.
    Column("settled", Boolean(), nullable=False, server_default="false"),
    Column("settled_at", DateTime(timezone=True), nullable=True),
    # Later observations that disagreed with the settled rate (never rewritten).
    Column("conflict_count", Integer(), nullable=False, server_default="0"),
    Column("last_conflict_rate", String(64), nullable=True),
    Column("last_conflict_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "observation_count >= 1 AND revision_count >= 0 AND conflict_count >= 0",
        name="ck_futures_funding_rates_counts",
    ),
    CheckConstraint("interval_seconds > 0", name="ck_futures_funding_rates_interval"),
    CheckConstraint(
        "settled = (settled_at IS NOT NULL)",
        name="ck_futures_funding_rates_settled_at",
    ),
    Index("ix_futures_funding_rates_funding_time", "funding_time"),
)

__all__ = [
    "futures_catalog_poll_state",
    "futures_funding_rates",
    "futures_instrument_observations",
]
