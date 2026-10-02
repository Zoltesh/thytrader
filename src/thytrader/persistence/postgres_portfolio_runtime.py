"""In-transaction rows for deployed-portfolio runtime state and manager proposals (ADR 0091).

Callers own the transaction and lock order: the strategy row (when a proposal names one),
then the portfolio row, then the proposal row. Runtime writes are compare-and-set on the
runtime row's ``revision`` so the worker's baseline updates can never undo an operator's
breaker reset (or the reverse).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - runtime casts of database values.
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import func, insert, select, update

from thytrader.persistence.schema import deployments, portfolio_proposals, portfolio_runtime
from thytrader.portfolios.models import (
    BreakerReason,
    JournalChannel,
    PortfolioRuntimeState,
)
from thytrader.portfolios.proposals import (
    REBALANCE_BUDGET_WINDOW,
    DecidedBy,
    Proposal,
    ProposalChange,
    ProposalEvidence,
    ProposalKind,
    ProposalStatus,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection

_SUBMITTERS = Literal["manager", "operator"]

_CHANGE = TypeAdapter[ProposalChange](ProposalChange)
_EVIDENCE = TypeAdapter[tuple[ProposalEvidence, ...]](tuple[ProposalEvidence, ...])
_OCCUPIED = ("running", "paused")


def _text(value: Decimal | None) -> str | None:
    """Canonical plain text for an optional decimal."""
    return None if value is None else format(value, "f")


def _decimal(value: object) -> Decimal | None:
    """Parse an optional stored decimal."""
    return None if value is None else Decimal(str(value))


def runtime_from_row(row: RowMapping) -> PortfolioRuntimeState:
    """Rebuild one runtime state from its row."""
    return PortfolioRuntimeState(
        portfolio_id=UUID(cast("str", row["portfolio_id"])),
        run_started_at=cast("datetime | None", row["run_started_at"]),
        breaker_reason=cast("BreakerReason | None", row["breaker_reason"]),
        breaker_detail=cast("str | None", row["breaker_detail"]),
        breaker_latched_at=cast("datetime | None", row["breaker_latched_at"]),
        day_open_equity=_decimal(row["day_open_equity"]),
        day_open_at=cast("datetime | None", row["day_open_at"]),
        high_water_mark_equity=_decimal(row["high_water_mark_equity"]),
        last_equity=_decimal(row["last_equity"]),
        last_evaluated_at=cast("datetime | None", row["last_evaluated_at"]),
        revision=int(cast("int", row["revision"])),
    )


def _runtime_values(
    state: PortfolioRuntimeState, *, revision: int, now: datetime
) -> dict[str, object]:
    """Column values for one runtime row at ``revision``."""
    return {
        "portfolio_id": str(state.portfolio_id),
        "run_started_at": state.run_started_at,
        "breaker_reason": state.breaker_reason,
        "breaker_detail": state.breaker_detail,
        "breaker_latched_at": state.breaker_latched_at,
        "day_open_equity": _text(state.day_open_equity),
        "day_open_at": state.day_open_at,
        "high_water_mark_equity": _text(state.high_water_mark_equity),
        "last_equity": _text(state.last_equity),
        "last_evaluated_at": state.last_evaluated_at,
        "revision": revision,
        "updated_at": now,
    }


async def runtime_rows(
    connection: AsyncConnection, portfolio_ids: Sequence[str]
) -> dict[UUID, PortfolioRuntimeState]:
    """Runtime states of the given portfolios that have a row."""
    if not portfolio_ids:
        return {}
    rows = (
        (
            await connection.execute(
                select(portfolio_runtime).where(
                    portfolio_runtime.c.portfolio_id.in_(list(portfolio_ids))
                )
            )
        )
        .mappings()
        .all()
    )
    states = (runtime_from_row(row) for row in rows)
    return {state.portfolio_id: state for state in states}


async def compare_and_set_runtime(
    connection: AsyncConnection,
    state: PortfolioRuntimeState,
    *,
    expected_revision: int,
    now: datetime,
) -> PortfolioRuntimeState | None:
    """Insert (expected 0) or update (expected N) the runtime row; None when it moved on."""
    revision = expected_revision + 1
    values = _runtime_values(state, revision=revision, now=now)
    if expected_revision == 0:
        exists = (
            await connection.execute(
                select(portfolio_runtime.c.portfolio_id)
                .where(portfolio_runtime.c.portfolio_id == str(state.portfolio_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if exists is not None:
            return None
        await connection.execute(insert(portfolio_runtime).values(**values))
    else:
        result = await connection.execute(
            update(portfolio_runtime)
            .where(
                portfolio_runtime.c.portfolio_id == str(state.portfolio_id),
                portfolio_runtime.c.revision == expected_revision,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            return None
    return PortfolioRuntimeState(
        portfolio_id=state.portfolio_id,
        run_started_at=state.run_started_at,
        breaker_reason=state.breaker_reason,
        breaker_detail=state.breaker_detail,
        breaker_latched_at=state.breaker_latched_at,
        day_open_equity=state.day_open_equity,
        day_open_at=state.day_open_at,
        high_water_mark_equity=state.high_water_mark_equity,
        last_equity=state.last_equity,
        last_evaluated_at=state.last_evaluated_at,
        revision=revision,
    )


async def occupied_deployment_count(
    connection: AsyncConnection, portfolio_id: str, *, strategy_id: str | None = None
) -> int:
    """How many running or paused bots carry this portfolio (optionally one strategy)."""
    statement = (
        select(func.count())
        .select_from(deployments)
        .where(deployments.c.portfolio_id == portfolio_id, deployments.c.status.in_(_OCCUPIED))
    )
    if strategy_id is not None:
        statement = statement.where(deployments.c.strategy_id == strategy_id)
    return int((await connection.execute(statement)).scalar_one())


def proposal_values(proposal: Proposal) -> dict[str, object]:
    """Column values for one proposal row."""
    return {
        "proposal_id": proposal.proposal_id,
        "portfolio_id": str(proposal.portfolio_id),
        "kind": proposal.kind,
        "status": proposal.status,
        "summary": proposal.summary,
        "rationale": proposal.rationale,
        "change": _CHANGE.dump_json(proposal.change).decode("utf-8"),
        "evidence": _EVIDENCE.dump_json(proposal.evidence, exclude_none=True).decode("utf-8"),
        "base_revision": proposal.base_revision,
        "submitted_by": proposal.submitted_by,
        "channel": proposal.channel,
        "approval_reason": proposal.approval_reason,
        "weight_moved": proposal.weight_moved,
        "created_at": proposal.created_at,
        "expires_at": proposal.expires_at,
        "decided_at": proposal.decided_at,
        "decided_by": proposal.decided_by,
        "decision_note": proposal.decision_note,
        "auto_applied": proposal.auto_applied,
        "applied_revision": proposal.applied_revision,
        "failure_code": proposal.failure_code,
        "failure_message": proposal.failure_message,
    }


def proposal_from_row(row: RowMapping) -> Proposal:
    """Rebuild one proposal from its row (re-validated through the domain model)."""
    return Proposal(
        proposal_id=cast("UUID", row["proposal_id"]),
        portfolio_id=UUID(cast("str", row["portfolio_id"])),
        kind=cast("ProposalKind", row["kind"]),
        status=cast("ProposalStatus", row["status"]),
        summary=cast("str", row["summary"]),
        rationale=cast("str", row["rationale"]),
        change=_CHANGE.validate_json(cast("str", row["change"])),
        evidence=_EVIDENCE.validate_json(cast("str", row["evidence"])),
        base_revision=int(cast("int", row["base_revision"])),
        submitted_by=cast("_SUBMITTERS", row["submitted_by"]),
        channel=cast("JournalChannel", row["channel"]),
        approval_reason=cast("str | None", row["approval_reason"]),
        weight_moved=cast("str | None", row["weight_moved"]),
        created_at=cast("datetime", row["created_at"]),
        expires_at=cast("datetime", row["expires_at"]),
        decided_at=cast("datetime | None", row["decided_at"]),
        decided_by=cast("DecidedBy | None", row["decided_by"]),
        decision_note=cast("str | None", row["decision_note"]),
        auto_applied=bool(row["auto_applied"]),
        applied_revision=cast("int | None", row["applied_revision"]),
        failure_code=cast("str | None", row["failure_code"]),
        failure_message=cast("str | None", row["failure_message"]),
    )


async def insert_proposal(connection: AsyncConnection, proposal: Proposal) -> None:
    """Insert one new proposal row."""
    await connection.execute(insert(portfolio_proposals).values(**proposal_values(proposal)))


async def update_proposal(connection: AsyncConnection, proposal: Proposal) -> None:
    """Rewrite one proposal row's outcome columns."""
    values = proposal_values(proposal)
    for key in ("proposal_id", "portfolio_id", "created_at"):
        values.pop(key)
    await connection.execute(
        update(portfolio_proposals)
        .where(portfolio_proposals.c.proposal_id == proposal.proposal_id)
        .values(**values)
    )


async def locked_proposal(
    connection: AsyncConnection, portfolio_id: str, proposal_id: UUID
) -> Proposal | None:
    """Lock and read one proposal of the portfolio."""
    row = (
        (
            await connection.execute(
                select(portfolio_proposals)
                .where(
                    portfolio_proposals.c.proposal_id == proposal_id,
                    portfolio_proposals.c.portfolio_id == portfolio_id,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else proposal_from_row(row)


async def auto_moved_since(
    connection: AsyncConnection, portfolio_id: str, *, now: datetime
) -> Decimal:
    """Weight moved by auto-applied rebalances in the trailing budget window."""
    rows = (
        await connection.execute(
            select(portfolio_proposals.c.weight_moved).where(
                portfolio_proposals.c.portfolio_id == portfolio_id,
                portfolio_proposals.c.kind == "rebalance",
                portfolio_proposals.c.status == "applied",
                portfolio_proposals.c.auto_applied.is_(True),
                portfolio_proposals.c.decided_at >= now - REBALANCE_BUDGET_WINDOW,
                portfolio_proposals.c.weight_moved.is_not(None),
            )
        )
    ).scalars()
    return sum((Decimal(cast("str", value)) for value in rows), Decimal(0))
