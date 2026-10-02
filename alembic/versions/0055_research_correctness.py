"""Store backtest diagnostics, admit stop-only live protection, and allow no take-profit.

Revision ID: 0055
Revises: 0054
Create Date: 2026-10-02

ADR 0090. Three forward-safe, additive changes:

* ``published_backtest_results.diagnostics_json`` (nullable Text) holds the bounded
  entry-funnel counters of a result **beside** ``canonical_result``, so no result
  fingerprint changes. Rows written before this revision stay NULL ("not recorded").
* ``ck_order_intents_kind`` admits ``stop_limit``: the live stop-only protective order a
  ``take_profit: {"kind": "none"}`` book rests instead of a TP/SL trigger bracket.
* ``execution_positions.target_price`` becomes nullable: a book without a take-profit has
  no target. Every existing row keeps its value.

Downgrade refuses to run while a position without a target or a ``stop_limit`` intent
exists, because older code cannot represent either.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

_NEW_KIND = "kind IN ('post_only_limit', 'marketable', 'trigger_bracket', 'stop_limit')"
_OLD_KIND = "kind IN ('post_only_limit', 'marketable', 'trigger_bracket')"


def upgrade() -> None:
    """Add the diagnostics column, widen the intent kind CHECK, and relax target_price."""
    op.add_column(
        "published_backtest_results",
        sa.Column(
            "diagnostics_json",
            sa.Text(),
            nullable=True,
            comment=(
                "thytrader-backtest-diagnostics-v1 entry-funnel counters; outside the "
                "canonical result bytes and fingerprint (ADR 0090)."
            ),
        ),
    )
    op.drop_constraint("ck_order_intents_kind", "order_intents", type_="check")
    op.create_check_constraint("ck_order_intents_kind", "order_intents", _NEW_KIND)
    op.alter_column(
        "execution_positions",
        "target_price",
        existing_type=sa.String(length=64),
        nullable=True,
    )


def downgrade() -> None:
    """Restore the 0054 shape when no row depends on the 0055 semantics."""
    bind = op.get_bind()
    untargeted = bind.execute(
        sa.text("SELECT COUNT(*) FROM execution_positions WHERE target_price IS NULL")
    ).scalar_one()
    stop_limits = bind.execute(
        sa.text("SELECT COUNT(*) FROM order_intents WHERE kind = 'stop_limit'")
    ).scalar_one()
    if untargeted or stop_limits:
        raise RuntimeError(
            "Cannot downgrade 0055: positions without a take-profit or stop_limit intents "
            "exist and older code cannot represent them."
        )
    op.alter_column(
        "execution_positions",
        "target_price",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.drop_constraint("ck_order_intents_kind", "order_intents", type_="check")
    op.create_check_constraint("ck_order_intents_kind", "order_intents", _OLD_KIND)
    op.drop_column("published_backtest_results", "diagnostics_json")
