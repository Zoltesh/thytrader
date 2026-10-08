"""Fleet entry-inhibition and fleet control operation tables."""

from __future__ import annotations

from sqlalchemy import (
    UUID,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from thytrader.persistence.schema_metadata import metadata

fleet_entry_inhibition = Table(
    "fleet_entry_inhibition",
    metadata,
    Column("mode", String(16), primary_key=True),
    Column("inhibited", Boolean(), nullable=False),
    Column("revision", Integer(), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("reason", Text(), nullable=True),
    CheckConstraint("mode IN ('paper', 'live')", name="ck_fleet_entry_inhibition_mode"),
)

fleet_control_operations = Table(
    "fleet_control_operations",
    metadata,
    Column("id", UUID(), primary_key=True),
    Column("idempotency_key", String(128), nullable=False),
    Column("action", String(32), nullable=False),
    Column("mode", String(16), nullable=False),
    Column("status", String(32), nullable=False),
    Column("request_fingerprint", String(4000), nullable=False),
    Column("request_json", Text(), nullable=False),
    Column("result_json", Text(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "action IN ('disarm', 'managed_stop', 'flatten', 'rearm')",
        name="ck_fleet_control_operations_action",
    ),
    CheckConstraint("mode IN ('paper', 'live', 'all')", name="ck_fleet_control_operations_mode"),
    CheckConstraint(
        "status IN ('pending', 'accepted', 'partial', 'completed', 'rejected')",
        name="ck_fleet_control_operations_status",
    ),
    UniqueConstraint("idempotency_key", name="ux_fleet_control_operations_idempotency_key"),
)

__all__ = [
    "fleet_control_operations",
    "fleet_entry_inhibition",
]
