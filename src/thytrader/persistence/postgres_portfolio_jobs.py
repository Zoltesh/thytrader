"""Portfolio backtest job and result rows for the PostgreSQL portfolio store (ADR 0088).

:class:`~thytrader.persistence.postgres_portfolios.PostgresPortfolioStore` owns every
connection and transaction; this module reads and maps job rows inside the caller's
transaction and builds the job and published-result statements its methods execute.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import Update, select, update
from sqlalchemy.dialects.postgresql import Insert, insert

from thytrader.persistence.schema import portfolio_backtest_jobs, published_portfolio_backtests
from thytrader.portfolios.backtest import PortfolioBacktestJob, job_expiry
from thytrader.portfolios.models import PortfolioError
from thytrader.research.jobs import ResearchJobStatus

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import Select
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection

    from thytrader.portfolios.backtest import (
        PortfolioBacktestListing,
        PortfolioBacktestPlan,
        PortfolioBacktestResult,
    )
    from thytrader.portfolios.models import MutationContext

_ACTIVE = (ResearchJobStatus.QUEUED.value, ResearchJobStatus.RUNNING.value)


async def _locked_job_row(connection: AsyncConnection, job_id: UUID) -> RowMapping:
    """Lock one job row or raise when it no longer exists (portfolio deleted)."""
    statement = (
        select(portfolio_backtest_jobs)
        .where(portfolio_backtest_jobs.c.job_id == job_id)
        .with_for_update()
    )
    row = (await connection.execute(statement)).mappings().one_or_none()
    if row is None:
        raise PortfolioError("Portfolio backtest job no longer exists.")
    return row


async def _job(connection: AsyncConnection, job_id: UUID) -> PortfolioBacktestJob:
    """Read one job row as its record."""
    statement = select(portfolio_backtest_jobs).where(portfolio_backtest_jobs.c.job_id == job_id)
    row = (await connection.execute(statement)).mappings().one()
    return _job_from_row(row)


def _job_from_row(row: RowMapping) -> PortfolioBacktestJob:
    """Map one job row into its record."""
    return PortfolioBacktestJob(
        job_id=cast("UUID", row["job_id"]),
        portfolio_id=UUID(cast("str", row["portfolio_id"])),
        portfolio_revision=int(cast("int", row["portfolio_revision"])),
        status=ResearchJobStatus(cast("str", row["status"])),
        created_at=cast("datetime", row["created_at"]),
        updated_at=cast("datetime", row["updated_at"]),
        expires_at=cast("datetime", row["expires_at"]),
        evaluation_start=cast("datetime", row["evaluation_start"]),
        evaluation_end=cast("datetime", row["evaluation_end"]),
        sleeve_count=int(cast("int", row["sleeve_count"])),
        progress_current=int(cast("int", row["progress_current"])),
        progress_total=int(cast("int", row["progress_total"])),
        error_message=cast("str | None", row["error_message"]),
        failed_detail=cast("str | None", row["failed_detail"]),
        result_fingerprint=cast("str | None", row["result_fingerprint"]),
    )


def _job_values(
    plan: PortfolioBacktestPlan, *, job_id: UUID, now: datetime, context: MutationContext
) -> dict[str, object]:
    """Column values of one newly queued job for a resolved plan."""
    return {
        "job_id": job_id,
        "portfolio_id": str(plan.portfolio_id),
        "portfolio_revision": plan.portfolio_revision,
        "status": ResearchJobStatus.QUEUED.value,
        "payload": plan.model_dump_json(),
        "actor": context.actor,
        "channel": context.channel,
        "evaluation_start": plan.evaluation_start,
        "evaluation_end": plan.evaluation_end,
        "sleeve_count": len(plan.sleeves),
        "progress_current": 0,
        "progress_total": len(plan.sleeves) + 1,
        "created_at": now,
        "updated_at": now,
        "expires_at": job_expiry(now),
    }


def _result_insert(
    result: PortfolioBacktestResult,
    *,
    fingerprint: str,
    listing: PortfolioBacktestListing,
    canonical: bytes,
    now: datetime,
) -> Insert:
    """Publish one canonical result; an identical fingerprint is already stored."""
    return (
        insert(published_portfolio_backtests)
        .values(
            result_fingerprint=fingerprint,
            portfolio_id=str(result.portfolio_id),
            portfolio_revision=result.portfolio_revision,
            evaluation_start=result.evaluation_start,
            evaluation_end=result.evaluation_end,
            listing=listing.model_dump_json(),
            canonical_result=canonical.decode("utf-8"),
            published_at=now,
        )
        .on_conflict_do_nothing(index_elements=["result_fingerprint"])
    )


def _job_completion_update(job_id: UUID, *, fingerprint: str, now: datetime) -> Update:
    """Complete one job with its result fingerprint and full progress."""
    return (
        update(portfolio_backtest_jobs)
        .where(portfolio_backtest_jobs.c.job_id == job_id)
        .values(
            status=ResearchJobStatus.COMPLETED.value,
            result_fingerprint=fingerprint,
            progress_current=portfolio_backtest_jobs.c.progress_total,
            updated_at=now,
        )
    )


def _expire_jobs_update(now: datetime) -> Update:
    """Expire every queued or running job past its expiry."""
    return (
        update(portfolio_backtest_jobs)
        .where(
            portfolio_backtest_jobs.c.expires_at <= now,
            portfolio_backtest_jobs.c.status.in_(_ACTIVE),
        )
        .values(
            status=ResearchJobStatus.EXPIRED.value,
            error_message="Portfolio backtest expired.",
            updated_at=now,
        )
    )


def _results_page_select(portfolio_id: UUID, *, limit: int, offset: int) -> Select[Any]:
    """Select one page of a portfolio's result listings, newest first."""
    return (
        select(published_portfolio_backtests.c.listing)
        .where(published_portfolio_backtests.c.portfolio_id == str(portfolio_id))
        .order_by(
            published_portfolio_backtests.c.published_at.desc(),
            published_portfolio_backtests.c.result_fingerprint.asc(),
        )
        .limit(limit)
        .offset(offset)
    )
