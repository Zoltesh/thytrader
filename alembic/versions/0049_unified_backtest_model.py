"""Unified backtest model: drop the per-engine contract column and retired research rows.

Revision ID: 0049
Revises: 0048
Create Date: 2026-09-30

ADR 0083 collapses the per-version bar-backtest engines into one unversioned
``thytrader-backtest`` model. Canonical run, trace, result, and study documents now carry
``engine`` instead of an engine-contract selector, so documents written by the retired
engines can no longer be loaded or reverified. There is no evidence to preserve (0048
wiped all research rows and every later row was test data), so this revision:

* deletes every row from ``research_jobs``, ``research_study_strategies``,
  ``published_research_studies``, ``published_backtest_results``, and
  ``published_research_run_specs`` (children first; safe on an empty database);
* drops ``published_research_studies.engine_contract_version``.

Strategies, snapshots, dataset bindings, deployments and their ledgers, market data,
risk policies, credentials, settings, audit events, and journals are untouched: no
paper or live deployment references a research run or backtest result.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

_RESEARCH_TABLES_CHILD_FIRST: tuple[str, ...] = (
    "research_jobs",
    "research_study_strategies",
    "published_research_studies",
    "published_backtest_results",
    "published_research_run_specs",
)


def upgrade() -> None:
    """Remove documents the unified model cannot verify, then drop the engine column."""
    connection = op.get_bind()
    for table in _RESEARCH_TABLES_CHILD_FIRST:
        connection.execute(sa.text(f"DELETE FROM {table}"))  # noqa: S608 - fixed table names
    op.drop_column("published_research_studies", "engine_contract_version")


def downgrade() -> None:
    """Restore the column shape only; deleted research rows cannot be recovered."""
    op.add_column(
        "published_research_studies",
        sa.Column(
            "engine_contract_version",
            sa.String(64),
            nullable=False,
            server_default="thytrader-backtest",
        ),
    )
    op.alter_column("published_research_studies", "engine_contract_version", server_default=None)
