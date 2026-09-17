"""Research ops-contract v27 marker (async backtests and study summary).

Revision ID: 0040
Revises: 0039
Create Date: 2026-09-17
"""

from __future__ import annotations

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No schema change; ops contract v27 advertises async backtest jobs (ADR 0069)."""


def downgrade() -> None:
    """No schema change; downgrade is a no-op marker for the contract bump."""
