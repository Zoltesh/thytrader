"""Research ops-contract v25 marker (bar-backtest-v4 advertisement).

Revision ID: 0038
Revises: 0037
Create Date: 2026-09-17
"""

from __future__ import annotations

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No schema change; ops contract v25 advertises thytrader-bar-backtest-v4 (ADR 0066)."""


def downgrade() -> None:
    """No schema change; downgrade is a no-op marker for the contract bump."""
