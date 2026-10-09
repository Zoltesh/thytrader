"""Trade-reason and per-bar decision journal tables."""

from __future__ import annotations

from sqlalchemy import (
    UUID,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from thytrader.persistence.schema_metadata import metadata

trade_reason_records = Table(
    "trade_reason_records",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("origin", String(8), nullable=False),
    Column("intent_id", UUID(), nullable=False),
    Column("deployment_id", UUID(), nullable=False),
    Column("deployment_kind", String(16), nullable=False),
    Column("mode", String(8), nullable=False),
    Column("product_id", String(32), nullable=False),
    Column("purpose", String(32), nullable=False),
    Column("side", String(8), nullable=False),
    Column("strategy_id", UUID(), nullable=True),
    Column("strategy_fingerprint", String(71), nullable=True),
    Column("strategy_name", String(120), nullable=True),
    Column("signal_kind", String(32), nullable=False),
    Column("last_signal", String(32), nullable=True),
    Column("candle_starts_at", DateTime(timezone=True), nullable=False),
    Column("timeframe", String(8), nullable=True),
    Column("risk_decision", String(8), nullable=False),
    Column("risk_reason_code", String(64), nullable=False),
    Column("risk_detail", String(500), nullable=False),
    Column("policy_fingerprint", String(71), nullable=False),
    Column("policy_source", String(24), nullable=False),
    Column("notes_json", Text(), nullable=False, server_default="[]"),
    UniqueConstraint("intent_id", name="ux_trade_reason_records_intent_id"),
    CheckConstraint(
        "origin IN ('human', 'agent', 'runtime')",
        name="ck_trade_reason_origin",
    ),
    CheckConstraint(
        "deployment_kind IN ('strategy', 'discretionary')",
        name="ck_trade_reason_deployment_kind",
    ),
    CheckConstraint("mode IN ('paper', 'live')", name="ck_trade_reason_mode"),
    CheckConstraint("side IN ('buy', 'sell')", name="ck_trade_reason_side"),
    CheckConstraint(
        "purpose IN ("
        "'entry', 'take_profit', 'stop', 'time_exit', 'bracket', 'signal_exit', 'adoption'"
        ")",
        name="ck_trade_reason_purpose",
    ),
    CheckConstraint(
        "signal_kind IN ("
        "'strategy_entry', 'discretionary', 'take_profit', 'stop', 'time_exit', 'bracket', "
        "'signal_exit', 'adoption'"
        ")",
        name="ck_trade_reason_signal_kind",
    ),
    CheckConstraint(
        "risk_decision IN ('allow', 'deny')",
        name="ck_trade_reason_risk_decision",
    ),
    CheckConstraint(
        "policy_source IN ('compiled_default', 'published')",
        name="ck_trade_reason_policy_source",
    ),
    CheckConstraint(
        "timeframe IS NULL OR timeframe IN "
        "('1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d')",
        name="ck_trade_reason_timeframe",
    ),
)

Index(
    "ix_trade_reason_created_at_desc",
    trade_reason_records.c.created_at.desc(),
    trade_reason_records.c.id.desc(),
)

Index(
    "ix_trade_reason_deployment_created_at_desc",
    trade_reason_records.c.deployment_id,
    trade_reason_records.c.created_at.desc(),
)

bar_decisions = Table(
    "bar_decisions",
    metadata,
    Column("deployment_id", UUID(), primary_key=True),
    Column("product_id", String(32), primary_key=True),
    Column(
        "bar_starts_at",
        DateTime(timezone=True),
        primary_key=True,
        comment="UTC start of the completed decision bar this row explains.",
    ),
    Column("strategy_id", UUID(), nullable=True),
    Column("mode", String(8), nullable=False),
    Column("timeframe", String(8), nullable=False),
    Column("evaluated_at", DateTime(timezone=True), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("action", String(24), nullable=False),
    Column("reason_code", String(64), nullable=False),
    Column("intent_id", UUID(), nullable=True),
    Column("summary", String(500), nullable=False),
    Column(
        "payload_json",
        Text(),
        nullable=False,
        comment="Canonical thytrader-bar-decision-v1 JSON (rule tree, risk, orders, fills).",
    ),
    ForeignKeyConstraint(
        ["deployment_id"],
        ["deployments.id"],
        ondelete="CASCADE",
        name="fk_bar_decisions_deployment_id",
    ),
    CheckConstraint("mode IN ('paper', 'live')", name="ck_bar_decisions_mode"),
    CheckConstraint(
        "outcome IN ('entry_signal', 'no_signal', 'holding', 'exit', 'entry_blocked', "
        "'skipped', 'error')",
        name="ck_bar_decisions_outcome",
    ),
    CheckConstraint(
        "action IN ('none', 'intent_created', 'order_submitted', 'order_canceled', 'repriced')",
        name="ck_bar_decisions_action",
    ),
    CheckConstraint(
        "timeframe IN ('1m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d')",
        name="ck_bar_decisions_timeframe",
    ),
    comment="Bounded per-bar decision journal for paper and live strategy bots (ADR 0087).",
)

Index(
    "ix_bar_decisions_deployment_bar_desc",
    bar_decisions.c.deployment_id,
    bar_decisions.c.bar_starts_at.desc(),
    bar_decisions.c.product_id.desc(),
)

Index(
    "ix_bar_decisions_strategy_bar_desc",
    bar_decisions.c.strategy_id,
    bar_decisions.c.bar_starts_at.desc(),
)

Index("ix_bar_decisions_bar_starts_at", bar_decisions.c.bar_starts_at)

__all__ = [
    "bar_decisions",
    "trade_reason_records",
]
