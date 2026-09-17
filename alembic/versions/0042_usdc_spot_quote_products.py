"""USDC spot quote products ops-contract v29 marker.

Revision ID: 0042
Revises: 0041
Create Date: 2026-09-17
"""

from __future__ import annotations

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No schema change; ops contract v29 advertises USD and USDC spot quotes (ADR 0071)."""


def downgrade() -> None:
    """No schema change; downgrade is a no-op marker for the contract bump."""
