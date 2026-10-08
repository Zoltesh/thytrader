"""Journal entry for one completed portfolio backtest, shared by every portfolio store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.portfolios.backtest import PortfolioBacktestResult, backtest_journal_summary
from thytrader.portfolios.journal_changes import journal_entry
from thytrader.portfolios.models import JournalDetail, JournalEntry, MutationContext

if TYPE_CHECKING:
    from uuid import UUID


def backtest_journal_entry(
    *,
    portfolio_id: UUID,
    job_id: UUID,
    actor_context: MutationContext,
    result: PortfolioBacktestResult,
    result_fingerprint: str,
) -> JournalEntry:
    """Journal one completed backtest (shared by the PostgreSQL store)."""
    return journal_entry(
        portfolio_id,
        kind="backtest_run",
        context=actor_context,
        summary=backtest_journal_summary(result),
        revision=result.portfolio_revision,
        detail=JournalDetail(job_id=job_id, result_fingerprint=result_fingerprint),
    )
