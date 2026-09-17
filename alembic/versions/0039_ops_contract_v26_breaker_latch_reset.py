"""Operator ops-contract v26 marker (breaker latch reset advertisement).

Revision ID: 0039
Revises: 0038
Create Date: 2026-09-17
"""

from __future__ import annotations

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No schema change; ops contract v26 advertises breaker latch reset (ADR 0064)."""


def downgrade() -> None:
    """No schema change; downgrade is a no-op marker for the contract bump."""
