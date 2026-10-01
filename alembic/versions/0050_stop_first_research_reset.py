"""Stop-first same-bar exits: remove research rows computed under take-profit-first.

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-01

The ADR 0083 amendment (2026-10-01) changed the unified backtest model so a bar that
touches both the working stop and a resting take-profit resolves as the stop, and renamed
the disclosed validity code ``tp_before_stop_same_bar`` to ``stop_before_tp_same_bar``.
Results written before that change carry the retired code, so they fail reverification
(and the bounded list endpoint answered 503 for the whole page), and their trades were
computed under the retired take-profit-first rule. Every such row is test data, so this
revision deletes all research rows, children first, exactly like 0049:

* ``research_jobs``, ``research_study_strategies``, ``published_research_studies``,
  ``published_backtest_results``, and ``published_research_run_specs``.

Strategies, snapshots, dataset bindings, deployments and their ledgers, market data,
risk policies, credentials, settings, audit events, and journals are untouched. The
schema shape does not change.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0050"
down_revision = "0049"
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
    """Delete research documents computed under the retired take-profit-first rule."""
    connection = op.get_bind()
    for table in _RESEARCH_TABLES_CHILD_FIRST:
        connection.execute(sa.text(f"DELETE FROM {table}"))  # noqa: S608 - fixed table names


def downgrade() -> None:
    """No schema change to undo; deleted research rows cannot be recovered."""
