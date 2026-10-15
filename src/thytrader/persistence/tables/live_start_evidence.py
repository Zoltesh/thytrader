"""Evidence recorded with each live start (ADR 0143)."""

from __future__ import annotations

from sqlalchemy import UUID, Column, DateTime, ForeignKey, String, Table, Text

from thytrader.persistence.schema_metadata import metadata

live_start_evidence = Table(
    "live_start_evidence",
    metadata,
    Column(
        "deployment_id",
        UUID(),
        ForeignKey("deployments.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "strategy_fingerprint",
        String(71),
        nullable=False,
        comment="The started snapshot's fingerprint the evidence was gathered for.",
    ),
    Column(
        "assessed_at",
        DateTime(timezone=True),
        nullable=False,
        comment="UTC instant the live start's evidence was assessed.",
    ),
    Column(
        "evidence_json",
        Text(),
        nullable=False,
        comment="Canonical live-start evidence JSON (thytrader-live-start-evidence-v1).",
    ),
    comment="What a strategy had shown when each live bot started; first record kept.",
)

__all__ = [
    "live_start_evidence",
]
