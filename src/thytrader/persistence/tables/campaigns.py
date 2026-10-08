"""Research campaign state tables."""

from __future__ import annotations

from sqlalchemy import (
    UUID,
    Boolean,
    Column,
    DateTime,
    Index,
    String,
    Table,
    Text,
)

from thytrader.persistence.schema_metadata import metadata

research_campaigns = Table(
    "research_campaigns",
    metadata,
    Column("campaign_id", UUID(), primary_key=True),
    Column("manifest_json", Text(), nullable=False),
    Column("manifest_fingerprint", String(71), nullable=False),
    Column("state_json", Text(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed", Boolean(), nullable=False, server_default="false"),
)

Index(
    "ix_research_campaigns_pending", research_campaigns.c.completed, research_campaigns.c.updated_at
)

__all__ = [
    "research_campaigns",
]
