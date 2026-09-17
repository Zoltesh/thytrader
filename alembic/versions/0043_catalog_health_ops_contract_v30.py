"""Catalog health ops-contract v30 marker (bounded gaps, self-complete ingest).

Revision ID: 0043
Revises: 0042
Create Date: 2026-09-17

ADR 0072. Revises 0042 (USDC quote, PR 93). 0044 is reserved for research
jobs (PR 95). 0045 is reserved for ledger P0 (PR 97).
"""

from __future__ import annotations

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No schema change; ops contract v30 advertises catalog-health capabilities (ADR 0072)."""


def downgrade() -> None:
    """No schema change; downgrade is a no-op marker for the contract bump."""
