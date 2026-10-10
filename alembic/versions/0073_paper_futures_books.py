"""Paper futures books: bound contracts, funding entries, liquidation purpose (ADR 0129 §4).

Revision ID: 0073
Revises: 0072
Create Date: 2026-10-10

* ``deployment_instrument_contracts``: the futures contract a paper futures deployment bound
  at start (product, kind, underlying, contract size, USD settlement, expiry, catalog
  fingerprint) and its per-contract fee. One row per futures deployment; spot deployments
  have none.
* ``futures_funding_entries``: each funding hour applied to a book's cash, unique on
  deployment, product and funding hour, with the held quantity, mark and settled rate.
* ``ck_trade_reason_purpose`` and ``ck_trade_reason_signal_kind`` admit ``liquidation``,
  the protective exit a paper futures book submits when equity falls below maintenance.
  ``order_intents.purpose`` has no CHECK and needs no change.

Downgrade refuses while any funding entry, bound contract or liquidation why-trade row
exists, because older code cannot represent a futures book.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None

_OLD_PURPOSE = (
    "purpose IN ('entry', 'take_profit', 'stop', 'time_exit', 'bracket', 'signal_exit', 'adoption')"
)
_NEW_PURPOSE = (
    "purpose IN ("
    "'entry', 'take_profit', 'stop', 'time_exit', 'bracket', 'signal_exit', 'adoption', "
    "'liquidation'"
    ")"
)
_OLD_SIGNAL = (
    "signal_kind IN ("
    "'strategy_entry', 'discretionary', 'take_profit', 'stop', 'time_exit', 'bracket', "
    "'signal_exit', 'adoption'"
    ")"
)
_NEW_SIGNAL = (
    "signal_kind IN ("
    "'strategy_entry', 'discretionary', 'take_profit', 'stop', 'time_exit', 'bracket', "
    "'signal_exit', 'adoption', 'liquidation'"
    ")"
)
_FUTURES_ROWS = (
    "SELECT "
    "(SELECT COUNT(*) FROM deployment_instrument_contracts) "
    "+ (SELECT COUNT(*) FROM futures_funding_entries) "
    "+ (SELECT COUNT(*) FROM trade_reason_records "
    "WHERE purpose = 'liquidation' OR signal_kind = 'liquidation')"
)


def upgrade() -> None:
    """Create the two paper futures tables and admit the liquidation purpose."""
    op.create_table(
        "deployment_instrument_contracts",
        sa.Column("deployment_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("underlying", sa.String(32), nullable=False),
        sa.Column("contract_size", sa.String(64), nullable=False),
        sa.Column("settlement_currency", sa.String(8), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("listed_expiry", sa.Date(), nullable=False),
        sa.Column("catalog_fingerprint", sa.String(71), nullable=False),
        sa.Column("fee_per_contract", sa.String(64), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("deployment_id"),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "kind IN ('dated_future', 'perpetual_future')",
            name="ck_deployment_instrument_contracts_kind",
        ),
        sa.CheckConstraint(
            "settlement_currency = 'USD'",
            name="ck_deployment_instrument_contracts_settlement",
        ),
    )
    op.create_table(
        "futures_funding_entries",
        sa.Column("deployment_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.String(32), nullable=False),
        sa.Column("funding_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signed_quantity", sa.String(64), nullable=False),
        sa.Column("mark_price", sa.String(64), nullable=False),
        sa.Column("rate", sa.String(64), nullable=False),
        sa.Column("amount", sa.String(64), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("deployment_id", "product_id", "funding_time"),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
    )
    op.drop_constraint("ck_trade_reason_purpose", "trade_reason_records", type_="check")
    op.create_check_constraint("ck_trade_reason_purpose", "trade_reason_records", _NEW_PURPOSE)
    op.drop_constraint("ck_trade_reason_signal_kind", "trade_reason_records", type_="check")
    op.create_check_constraint("ck_trade_reason_signal_kind", "trade_reason_records", _NEW_SIGNAL)


def downgrade() -> None:
    """Restore 0072 when no futures book row depends on 0073."""
    futures = op.get_bind().execute(sa.text(_FUTURES_ROWS)).scalar_one()
    if futures:
        raise RuntimeError(
            "Cannot downgrade 0073: paper futures contracts, funding entries or liquidation "
            "why-trade rows exist, and older code cannot represent them."
        )
    op.drop_constraint("ck_trade_reason_signal_kind", "trade_reason_records", type_="check")
    op.create_check_constraint("ck_trade_reason_signal_kind", "trade_reason_records", _OLD_SIGNAL)
    op.drop_constraint("ck_trade_reason_purpose", "trade_reason_records", type_="check")
    op.create_check_constraint("ck_trade_reason_purpose", "trade_reason_records", _OLD_PURPOSE)
    op.drop_table("futures_funding_entries")
    op.drop_table("deployment_instrument_contracts")
