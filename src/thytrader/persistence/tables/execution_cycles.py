"""Execution-worker cycle timing table (ADR 0131)."""

from __future__ import annotations

from sqlalchemy import (
    UUID,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    Table,
    Text,
)

from thytrader.persistence.schema_metadata import metadata

execution_cycles = Table(
    "execution_cycles",
    metadata,
    Column("cycle_id", UUID(), primary_key=True),
    Column(
        "started_at",
        DateTime(timezone=True),
        nullable=False,
        comment="UTC instant the execution-worker cycle started.",
    ),
    Column(
        "interval_seconds",
        Integer(),
        nullable=False,
        comment="Configured execution-worker interval in force for this cycle.",
    ),
    Column(
        "completed_at",
        DateTime(timezone=True),
        nullable=True,
        comment="UTC completion instant; NULL while the cycle runs or if it never finished.",
    ),
    Column(
        "duration_seconds",
        Float(),
        nullable=True,
        comment="Wall-clock cycle duration; NULL until the cycle completes.",
    ),
    Column(
        "report_json",
        Text(),
        nullable=True,
        comment="Canonical execution cycle report JSON (phases, slowest books, venue calls).",
    ),
    CheckConstraint("interval_seconds >= 1", name="ck_execution_cycles_interval"),
    CheckConstraint(
        "(completed_at IS NULL) = (report_json IS NULL)",
        name="ck_execution_cycles_completion",
    ),
    Index("ix_execution_cycles_started_at", "started_at"),
    comment="Execution-worker cycle timing telemetry, retained for one day (ADR 0131).",
)

__all__ = [
    "execution_cycles",
]
