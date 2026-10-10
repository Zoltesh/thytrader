"""Record Coinbase CFM futures contract facts and hourly funding history (ADR 0126).

Revision ID: 0071
Revises: 0070
Create Date: 2026-10-10

Additive: three new tables, no existing row or constraint changes.

* ``futures_catalog_poll_state``: the poller's last attempt, last success and failure
  streak, so a stopped poller is visible.
* ``futures_instrument_observations``: one row per contract per distinct payload
  fingerprint (margin rates, sessions, maintenance windows, increments); unchanged polls
  only extend ``last_seen_at``.
* ``futures_funding_rates``: one row per contract and funding hour. A row settles when
  the listing names a later hour and is immutable after that; disagreeing later values
  are counted as conflicts, never rewritten.

Downgrade drops the tables. Funding history cannot be re-fetched from the venue, so a
downgrade loses it; it is refused while any funding row exists.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the futures poll-state, contract-observation and funding tables."""
    op.create_table(
        "futures_catalog_poll_state",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("listing_fingerprint", sa.String(length=71), nullable=True),
        sa.Column("contract_count", sa.Integer(), nullable=True),
        sa.Column("perpetual_count", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("provider"),
    )
    op.create_table(
        "futures_instrument_observations",
        sa.Column("product_id", sa.String(length=32), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("contract_code", sa.String(length=16), nullable=False),
        sa.Column("underlying", sa.String(length=32), nullable=False),
        sa.Column("settlement_currency", sa.String(length=8), nullable=False),
        sa.Column("contract_size", sa.String(length=64), nullable=False),
        sa.Column("price_increment", sa.String(length=64), nullable=False),
        sa.Column("base_increment", sa.String(length=64), nullable=False),
        sa.Column("base_min_size", sa.String(length=64), nullable=False),
        sa.Column("venue_expiry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("listed_expiry", sa.Date(), nullable=False),
        sa.Column("twenty_four_by_seven", sa.Boolean(), nullable=False),
        sa.Column("intraday_long_margin_rate", sa.String(length=64), nullable=True),
        sa.Column("intraday_short_margin_rate", sa.String(length=64), nullable=True),
        sa.Column("overnight_long_margin_rate", sa.String(length=64), nullable=True),
        sa.Column("overnight_short_margin_rate", sa.String(length=64), nullable=True),
        sa.Column("funding_interval_seconds", sa.Integer(), nullable=True),
        sa.Column("session_state", sa.String(length=64), nullable=True),
        sa.Column("maintenance_starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("maintenance_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("asset_type", sa.String(length=64), nullable=True),
        sa.Column("trading_enabled", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('dated_future', 'perpetual_future')",
            name="ck_futures_instrument_observations_kind",
        ),
        sa.CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="ck_futures_instrument_observations_seen_order",
        ),
        sa.PrimaryKeyConstraint("product_id", "first_seen_at"),
    )
    op.create_table(
        "futures_funding_rates",
        sa.Column("product_id", sa.String(length=32), nullable=False),
        sa.Column("funding_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rate", sa.String(length=64), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("revision_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("settled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conflict_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_conflict_rate", sa.String(length=64), nullable=True),
        sa.Column("last_conflict_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "observation_count >= 1 AND revision_count >= 0 AND conflict_count >= 0",
            name="ck_futures_funding_rates_counts",
        ),
        sa.CheckConstraint("interval_seconds > 0", name="ck_futures_funding_rates_interval"),
        sa.CheckConstraint(
            "settled = (settled_at IS NOT NULL)",
            name="ck_futures_funding_rates_settled_at",
        ),
        sa.PrimaryKeyConstraint("product_id", "funding_time"),
    )
    op.create_index(
        "ix_futures_funding_rates_funding_time",
        "futures_funding_rates",
        ["funding_time"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the futures tables, refusing while funding history would be lost."""
    rows = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM futures_funding_rates")).scalar()
    if rows:
        message = (
            "Refusing to drop futures_funding_rates: it holds recorded funding history "
            "that cannot be re-fetched from the venue."
        )
        raise RuntimeError(message)
    op.drop_index("ix_futures_funding_rates_funding_time", table_name="futures_funding_rates")
    op.drop_table("futures_funding_rates")
    op.drop_table("futures_instrument_observations")
    op.drop_table("futures_catalog_poll_state")
