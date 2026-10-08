"""Background worker liveness heartbeat tables."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    String,
    Table,
)

from thytrader.persistence.schema_metadata import metadata

worker_heartbeats = Table(
    "worker_heartbeats",
    metadata,
    Column("worker_name", String(32), primary_key=True),
    Column("heartbeat_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "worker_name IN ('portfolio_worker', 'market_data_worker', 'execution_worker')",
        name="ck_worker_heartbeats_name",
    ),
)

__all__ = [
    "worker_heartbeats",
]
