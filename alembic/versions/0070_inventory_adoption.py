"""Admit in-kind inventory adoption in the intent kind and why-trade CHECKs.

Revision ID: 0070
Revises: 0069
Create Date: 2026-10-09

ADR 0124. A live book can take ownership of coins the venue account already holds. The
adoption is recorded as an intent, a FILLED order and one applied fill of kind and
purpose ``adoption``, and it is never routed to a broker. Constraint-only and additive;
no existing row changes and no column is added:

* ``ck_order_intents_kind`` admits ``adoption``. ``order_intents.purpose`` and
  ``execution_orders.kind`` have no CHECK, so they need no change.
* ``ck_trade_reason_purpose`` and ``ck_trade_reason_signal_kind`` admit ``adoption``, the
  why-trade purpose and signal kind recorded for one adoption.

Downgrade refuses while any adoption intent, order or why-trade row exists, because older
code cannot represent an adopted book and would route an unknown order kind to the venue
as a post-only limit.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None

_OLD_KIND = "kind IN ('post_only_limit', 'marketable', 'trigger_bracket', 'stop_limit')"
_NEW_KIND = "kind IN ('post_only_limit', 'marketable', 'trigger_bracket', 'stop_limit', 'adoption')"
_OLD_PURPOSE = "purpose IN ('entry', 'take_profit', 'stop', 'time_exit', 'bracket', 'signal_exit')"
_NEW_PURPOSE = (
    "purpose IN ('entry', 'take_profit', 'stop', 'time_exit', 'bracket', 'signal_exit', 'adoption')"
)
_OLD_SIGNAL = (
    "signal_kind IN ("
    "'strategy_entry', 'discretionary', 'take_profit', 'stop', 'time_exit', 'bracket', "
    "'signal_exit'"
    ")"
)
_NEW_SIGNAL = (
    "signal_kind IN ("
    "'strategy_entry', 'discretionary', 'take_profit', 'stop', 'time_exit', 'bracket', "
    "'signal_exit', 'adoption'"
    ")"
)
_ADOPTION_ROWS = (
    "SELECT "
    "(SELECT COUNT(*) FROM order_intents WHERE kind = 'adoption' OR purpose = 'adoption') "
    "+ (SELECT COUNT(*) FROM execution_orders WHERE kind = 'adoption') "
    "+ (SELECT COUNT(*) FROM trade_reason_records "
    "WHERE purpose = 'adoption' OR signal_kind = 'adoption')"
)


def upgrade() -> None:
    """Widen the three CHECK constraints to admit ``adoption``."""
    op.drop_constraint("ck_order_intents_kind", "order_intents", type_="check")
    op.create_check_constraint("ck_order_intents_kind", "order_intents", _NEW_KIND)
    op.drop_constraint("ck_trade_reason_purpose", "trade_reason_records", type_="check")
    op.create_check_constraint("ck_trade_reason_purpose", "trade_reason_records", _NEW_PURPOSE)
    op.drop_constraint("ck_trade_reason_signal_kind", "trade_reason_records", type_="check")
    op.create_check_constraint("ck_trade_reason_signal_kind", "trade_reason_records", _NEW_SIGNAL)


def downgrade() -> None:
    """Restore the 0069 constraints when no row depends on adoption."""
    adopted = op.get_bind().execute(sa.text(_ADOPTION_ROWS)).scalar_one()
    if adopted:
        raise RuntimeError(
            "Cannot downgrade 0070: inventory adoption intents, orders or why-trade rows "
            "exist, and older code cannot represent them."
        )
    op.drop_constraint("ck_trade_reason_signal_kind", "trade_reason_records", type_="check")
    op.create_check_constraint("ck_trade_reason_signal_kind", "trade_reason_records", _OLD_SIGNAL)
    op.drop_constraint("ck_trade_reason_purpose", "trade_reason_records", type_="check")
    op.create_check_constraint("ck_trade_reason_purpose", "trade_reason_records", _OLD_PURPOSE)
    op.drop_constraint("ck_order_intents_kind", "order_intents", type_="check")
    op.create_check_constraint("ck_order_intents_kind", "order_intents", _OLD_KIND)
