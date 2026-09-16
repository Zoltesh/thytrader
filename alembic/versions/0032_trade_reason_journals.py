"""Create durable why-trade journal rows keyed by order intent.

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

_SIGNAL = (
    "signal_kind IN ("
    "'strategy_entry', 'discretionary', 'take_profit', 'stop', 'time_exit', 'bracket'"
    ")"
)
_PURPOSE = "purpose IN ('entry', 'take_profit', 'stop', 'time_exit', 'bracket')"
_TIMEFRAME = (
    "timeframe IS NULL OR timeframe IN ('1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d')"
)


def upgrade() -> None:
    """Add append-only trade-reason records keyed by intent id."""
    op.create_table(
        "trade_reason_records",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin", sa.String(length=8), nullable=False),
        sa.Column("intent_id", sa.UUID(), nullable=False),
        sa.Column("deployment_id", sa.UUID(), nullable=False),
        sa.Column("deployment_kind", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("product_id", sa.String(length=32), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("strategy_id", sa.UUID(), nullable=True),
        sa.Column("strategy_fingerprint", sa.String(length=71), nullable=True),
        sa.Column("strategy_name", sa.String(length=120), nullable=True),
        sa.Column("strategy_version", sa.Integer(), nullable=True),
        sa.Column("signal_kind", sa.String(length=32), nullable=False),
        sa.Column("last_signal", sa.String(length=32), nullable=True),
        sa.Column("candle_starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=True),
        sa.Column("risk_decision", sa.String(length=8), nullable=False),
        sa.Column("risk_reason_code", sa.String(length=64), nullable=False),
        sa.Column("risk_detail", sa.String(length=500), nullable=False),
        sa.Column("policy_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("policy_source", sa.String(length=24), nullable=False),
        sa.Column("notes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("intent_id", name="ux_trade_reason_records_intent_id"),
        sa.CheckConstraint(
            "origin IN ('human', 'agent', 'runtime')",
            name="ck_trade_reason_origin",
        ),
        sa.CheckConstraint(
            "deployment_kind IN ('strategy', 'discretionary')",
            name="ck_trade_reason_deployment_kind",
        ),
        sa.CheckConstraint("mode IN ('paper', 'live')", name="ck_trade_reason_mode"),
        sa.CheckConstraint("side IN ('buy', 'sell')", name="ck_trade_reason_side"),
        sa.CheckConstraint(_PURPOSE, name="ck_trade_reason_purpose"),
        sa.CheckConstraint(_SIGNAL, name="ck_trade_reason_signal_kind"),
        sa.CheckConstraint(
            "risk_decision IN ('allow', 'deny')",
            name="ck_trade_reason_risk_decision",
        ),
        sa.CheckConstraint(
            "policy_source IN ('compiled_default', 'published')",
            name="ck_trade_reason_policy_source",
        ),
        sa.CheckConstraint(_TIMEFRAME, name="ck_trade_reason_timeframe"),
    )
    op.create_index(
        "ix_trade_reason_created_at_desc",
        "trade_reason_records",
        [sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_trade_reason_deployment_created_at_desc",
        "trade_reason_records",
        ["deployment_id", sa.text("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Drop why-trade journal tables."""
    op.drop_index("ix_trade_reason_deployment_created_at_desc", "trade_reason_records")
    op.drop_index("ix_trade_reason_created_at_desc", "trade_reason_records")
    op.drop_table("trade_reason_records")
