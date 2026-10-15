"""Stop reporting cross-market full windows as the catalog's mean out-of-sample return.

Revision ID: 0077
Revises: 0076
ADR 0044 aggregate honesty. Cross-market studies score one full window per market; studies
assembled before the ``full_window_*`` aggregate aliases counted those windows under
``oos_*``, and the catalog copied that mean into ``mean_oos_return_fraction``. The column is
a derived listing summary: the canonical study documents (and their fingerprints) are not
touched, and their windows' roles still show what was scored.
"""

import sqlalchemy as sa

from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Clear the mislabelled out-of-sample mean of every cross-market catalog row."""
    op.execute(
        sa.text(
            "UPDATE published_research_studies SET mean_oos_return_fraction = NULL "
            "WHERE kind = 'cross_market'"
        )
    )


def downgrade() -> None:
    """Leave the honest value: the old figure was never an out-of-sample return."""
