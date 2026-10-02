"""Create the bounded per-bar decision journal for paper and live strategy bots.

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-02

One row per ``(deployment_id, product_id, bar_starts_at)`` explains what a running
bot decided on one completed bar and why (ADR 0087): outcome, action, the entry-rule
tree with the exact values each leaf read, the risk verdict, linked orders and fills,
the close price, and the end-of-bar position. Writes are upserts, so restart replays
never duplicate a bar. Rows cascade with their deployment and are pruned by the
execution worker (newest 20,000 per bot, at most 180 days). The table starts empty;
no existing row is rewritten.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the decision journal table and its paging/retention indexes."""
    op.create_table(
        "bar_decisions",
        sa.Column("deployment_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.String(length=32), nullable=False),
        sa.Column(
            "bar_starts_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="UTC start of the completed decision bar this row explains.",
        ),
        sa.Column("strategy_id", sa.UUID(), nullable=True),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("intent_id", sa.UUID(), nullable=True),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column(
            "payload_json",
            sa.Text(),
            nullable=False,
            comment="Canonical thytrader-bar-decision-v1 JSON (rule tree, risk, orders, fills).",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["deployments.id"],
            ondelete="CASCADE",
            name="fk_bar_decisions_deployment_id",
        ),
        sa.PrimaryKeyConstraint("deployment_id", "product_id", "bar_starts_at"),
        sa.CheckConstraint("mode IN ('paper', 'live')", name="ck_bar_decisions_mode"),
        sa.CheckConstraint(
            "outcome IN ('entry_signal', 'no_signal', 'holding', 'exit', 'entry_blocked', "
            "'skipped', 'error')",
            name="ck_bar_decisions_outcome",
        ),
        sa.CheckConstraint(
            "action IN ('none', 'intent_created', 'order_submitted', 'order_canceled', 'repriced')",
            name="ck_bar_decisions_action",
        ),
        sa.CheckConstraint(
            "timeframe IN ('1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d')",
            name="ck_bar_decisions_timeframe",
        ),
        comment="Bounded per-bar decision journal for paper and live strategy bots (ADR 0087).",
    )
    op.create_index(
        "ix_bar_decisions_deployment_bar_desc",
        "bar_decisions",
        ["deployment_id", sa.text("bar_starts_at DESC"), sa.text("product_id DESC")],
    )
    op.create_index(
        "ix_bar_decisions_strategy_bar_desc",
        "bar_decisions",
        ["strategy_id", sa.text("bar_starts_at DESC")],
    )
    op.create_index("ix_bar_decisions_bar_starts_at", "bar_decisions", ["bar_starts_at"])


def downgrade() -> None:
    """Drop the decision journal (explanations only; no trading state is lost)."""
    op.drop_index("ix_bar_decisions_bar_starts_at", table_name="bar_decisions")
    op.drop_index("ix_bar_decisions_strategy_bar_desc", table_name="bar_decisions")
    op.drop_index("ix_bar_decisions_deployment_bar_desc", table_name="bar_decisions")
    op.drop_table("bar_decisions")
