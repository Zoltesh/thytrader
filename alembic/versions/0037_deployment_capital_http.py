"""Deployment capital accounting HTTP contract marker.

Revision ID: 0037
Revises: 0035
Create Date: 2026-09-17

No new columns: ADR 0058 / Alembic 0035 already persist capital accounting on
``deployments``. This revision marks the ops-contract v24 slice that exposes the
``capital`` block on ``GET/POST /api/v1/deployments`` ([ADR 0065]).
"""

from __future__ import annotations

revision = "0037"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op schema marker for deployment capital HTTP exposure."""


def downgrade() -> None:
    """No-op schema marker for deployment capital HTTP exposure."""
