"""Signal-based exits: a durable exit marker on positions and the signal_exit purpose.

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-02

ADR 0093. Additive only; no existing row changes:

* ``execution_positions.signal_exit_bar`` (nullable timestamptz) is the UTC start of the
  closed bar whose ``exits.signal_exit`` rule matched. A book carrying it keeps exiting
  (protection cancelled, then a marketable cover) on every cycle until it is flat, so a
  venue cancel that completes between bars is followed by the exit, never by a freshly
  rested bracket. Every existing position stays NULL (no signal exit pending).
* ``ck_trade_reason_purpose`` and ``ck_trade_reason_signal_kind`` admit ``signal_exit``,
  the why-trade purpose and signal kind of the marketable exit that rule sends.

Downgrade refuses while a position carries the marker or a ``signal_exit`` why-trade row
exists, because older code cannot represent either.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None

_OLD_PURPOSE = "purpose IN ('entry', 'take_profit', 'stop', 'time_exit', 'bracket')"
_NEW_PURPOSE = "purpose IN ('entry', 'take_profit', 'stop', 'time_exit', 'bracket', 'signal_exit')"
_OLD_SIGNAL = (
    "signal_kind IN ("
    "'strategy_entry', 'discretionary', 'take_profit', 'stop', 'time_exit', 'bracket'"
    ")"
)
_NEW_SIGNAL = (
    "signal_kind IN ("
    "'strategy_entry', 'discretionary', 'take_profit', 'stop', 'time_exit', 'bracket', "
    "'signal_exit'"
    ")"
)


def upgrade() -> None:
    """Add the position exit marker and widen the why-trade purpose CHECKs."""
    op.add_column(
        "execution_positions",
        sa.Column(
            "signal_exit_bar",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "UTC start of the closed bar whose exits.signal_exit rule matched; the book "
                "keeps exiting until flat (ADR 0093)."
            ),
        ),
    )
    op.drop_constraint("ck_trade_reason_purpose", "trade_reason_records", type_="check")
    op.create_check_constraint("ck_trade_reason_purpose", "trade_reason_records", _NEW_PURPOSE)
    op.drop_constraint("ck_trade_reason_signal_kind", "trade_reason_records", type_="check")
    op.create_check_constraint("ck_trade_reason_signal_kind", "trade_reason_records", _NEW_SIGNAL)


def downgrade() -> None:
    """Restore the 0057 shape when no row depends on the 0058 semantics."""
    bind = op.get_bind()
    marked = bind.execute(
        sa.text("SELECT COUNT(*) FROM execution_positions WHERE signal_exit_bar IS NOT NULL")
    ).scalar_one()
    journaled = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM trade_reason_records "
            "WHERE purpose = 'signal_exit' OR signal_kind = 'signal_exit'"
        )
    ).scalar_one()
    if marked or journaled:
        raise RuntimeError(
            "Cannot downgrade 0058: a position is exiting on a signal or signal_exit "
            "why-trade rows exist, and older code cannot represent them."
        )
    op.drop_constraint("ck_trade_reason_signal_kind", "trade_reason_records", type_="check")
    op.create_check_constraint("ck_trade_reason_signal_kind", "trade_reason_records", _OLD_SIGNAL)
    op.drop_constraint("ck_trade_reason_purpose", "trade_reason_records", type_="check")
    op.create_check_constraint("ck_trade_reason_purpose", "trade_reason_records", _OLD_PURPOSE)
    op.drop_column("execution_positions", "signal_exit_bar")
