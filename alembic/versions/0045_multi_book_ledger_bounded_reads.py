"""Multi-book ledger and bounded deployment reads ops-contract v32 marker.

Revision ID: 0045
Revises: 0044
Create Date: 2026-09-17
"""

from __future__ import annotations

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No schema change; ops contract v32 advertises ledger reads (ADR 0074)."""


def downgrade() -> None:
    """No schema change; downgrade is a no-op marker for the contract bump."""
