"""Fleet decision-log aggregation of the operator ``fleet-health`` report (ADR 0130).

Counts the ``entry_blocked`` and ``skipped`` reasons in the per-bar decision journal of
every running book over a trailing window and flags systemic blockers:

- ``evidence_block``: any evidence-type denial (``risk.fleet_entry_models``
  ``EVIDENCE_REASON_CODES``: missing marks or accounting, unknown collateral, venue balance
  or beta);
- ``sizing_skips``: a sizing skip (for example ``NOTIONAL_BELOW_MINIMUM``) on two or more
  bars;
- ``shared_reason``: any other reason on two or more bots, except routine skips (warmup,
  cooldown, pending entry, bar settling, catch-up, paused, stopped, max open positions);
- ``warmup_stuck``: a bot that skipped at least 12 bars for warmup in the window. History is
  prefetched before a bot evaluates, so a warmup that long means its indicators can never
  be computed (for example a period longer than the venue history).

Read-only; a journal outage is reported as unavailable, never as an empty fleet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from thytrader.execution.decision_store import DecisionStoreError, decision_storage_label
from thytrader.execution.decisions import (
    DECISION_PAGE_MAX_LIMIT,
    DecisionOutcome,
    DecisionSkipReason,
)
from thytrader.operator.fleet_health_models import (
    FleetDecisionLogPayload,
    FleetDecisionReasonPayload,
    FleetSystemicBlockerPayload,
)
from thytrader.risk.fleet_entry_models import EVIDENCE_REASON_CODES
from thytrader.trading.models import DeploymentStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from thytrader.execution.decision_store import DecisionJournalStore
    from thytrader.execution.decisions import BarDecision
    from thytrader.trading.models import Deployment

FLEET_DECISION_WINDOW_HOURS = 24
_MAX_PAGES = 5
WARMUP_STUCK_ROWS = 12
_SHOWN_IDS = 20
_OUTCOMES = (DecisionOutcome.ENTRY_BLOCKED, DecisionOutcome.SKIPPED)
_ROUTINE_SKIPS = frozenset(
    {
        DecisionSkipReason.WARMUP,
        DecisionSkipReason.COOLDOWN,
        DecisionSkipReason.PENDING_ENTRY,
        DecisionSkipReason.BAR_SETTLING,
        DecisionSkipReason.CATCH_UP,
        DecisionSkipReason.PAUSED,
        DecisionSkipReason.STOPPED,
        DecisionSkipReason.MAX_OPEN_POSITIONS,
    }
)

type _Key = tuple[str, str, str | None]


@dataclass(slots=True)
class _Group:
    """Rows sharing one outcome, reason code and skip reason."""

    rows: int = 0
    deployments: dict[UUID, None] = field(default_factory=dict)
    latest: datetime | None = None


async def fleet_decision_log(
    journal: DecisionJournalStore | None,
    deployments: Sequence[Deployment],
    *,
    now: datetime,
) -> FleetDecisionLogPayload:
    """Read every running book's recent blocked and skipped bars and aggregate them."""
    since = now - timedelta(hours=FLEET_DECISION_WINDOW_HOURS)
    running = tuple(item for item in deployments if item.status is DeploymentStatus.RUNNING)
    if journal is None or decision_storage_label(journal) == "unavailable":
        return FleetDecisionLogPayload(
            storage="unavailable",
            window_hours=FLEET_DECISION_WINDOW_HOURS,
            since=since,
            running_deployments=len(running),
            rows_read=0,
        )
    rows: list[BarDecision] = []
    truncated = False
    unreadable: list[UUID] = []
    for deployment in running:
        try:
            found, cut = await _recent_rows(journal, deployment.id, since=since)
        except DecisionStoreError:
            unreadable.append(deployment.id)
            continue
        rows.extend(found)
        truncated = truncated or cut
    reasons, systemic = aggregate_decisions(rows)
    return FleetDecisionLogPayload(
        storage="available",
        window_hours=FLEET_DECISION_WINDOW_HOURS,
        since=since,
        running_deployments=len(running),
        rows_read=len(rows),
        truncated=truncated,
        unreadable_deployment_ids=tuple(unreadable),
        reasons=reasons,
        systemic=systemic,
    )


async def _recent_rows(
    journal: DecisionJournalStore, deployment_id: UUID, *, since: datetime
) -> tuple[list[BarDecision], bool]:
    """Newest-first blocked/skipped rows closing at or after ``since``; true when bounded."""
    rows: list[BarDecision] = []
    cursor: str | None = None
    for _page in range(_MAX_PAGES):
        page = await journal.list_for_deployment(
            deployment_id, limit=DECISION_PAGE_MAX_LIMIT, cursor=cursor, outcomes=_OUTCOMES
        )
        for decision in page.decisions:
            if decision.bar_closes_at < since:
                return rows, False
            rows.append(decision)
        cursor = page.next_cursor
        if cursor is None:
            return rows, False
    return rows, True


def aggregate_decisions(
    rows: Sequence[BarDecision],
) -> tuple[tuple[FleetDecisionReasonPayload, ...], tuple[FleetSystemicBlockerPayload, ...]]:
    """Group rows by reason, most frequent first, and flag the systemic groups."""
    groups: dict[_Key, _Group] = {}
    for row in rows:
        skip = None if row.skip_reason is None else row.skip_reason.value
        group = groups.setdefault((row.outcome.value, row.reason_code, skip), _Group())
        group.rows += 1
        group.deployments.setdefault(row.deployment_id, None)
        if group.latest is None or row.bar_closes_at > group.latest:
            group.latest = row.bar_closes_at
    ordered = sorted(groups.items(), key=lambda item: (-item[1].rows, item[0]))
    reasons = tuple(_reason(key, group) for key, group in ordered)
    systemic = tuple(
        flagged for key, group in ordered if (flagged := _systemic(key, group)) is not None
    )
    stuck = _warmup_stuck(rows)
    return reasons, systemic if stuck is None else (*systemic, stuck)


def _warmup_stuck(rows: Sequence[BarDecision]) -> FleetSystemicBlockerPayload | None:
    """Bots whose warmup skips in the window reach ``WARMUP_STUCK_ROWS``."""
    counts: dict[UUID, int] = {}
    for row in rows:
        if row.skip_reason is DecisionSkipReason.WARMUP:
            counts[row.deployment_id] = counts.get(row.deployment_id, 0) + 1
    stuck = [deployment for deployment, count in counts.items() if count >= WARMUP_STUCK_ROWS]
    if not stuck:
        return None
    total = sum(counts[deployment] for deployment in stuck)
    return FleetSystemicBlockerPayload(
        kind="warmup_stuck",
        outcome="skipped",
        reason_code="WARMUP",
        rows=total,
        deployments=len(stuck),
        deployment_ids=tuple(stuck)[:_SHOWN_IDS],
        detail=(
            f"{len(stuck)} bot(s) skipped {total} bar(s) for indicator warmup in the window; "
            "their indicators may never become computable. Check the strategy's longest "
            "lookback against the venue history."
        ),
    )


def _reason(key: _Key, group: _Group) -> FleetDecisionReasonPayload:
    """One aggregated reason row."""
    outcome, code, skip = key
    latest = group.latest
    if latest is None:
        message = "A decision group always holds at least one row."
        raise ValueError(message)
    return FleetDecisionReasonPayload(
        outcome=_outcome(outcome),
        reason_code=code,
        skip_reason=skip,
        rows=group.rows,
        deployments=len(group.deployments),
        deployment_ids=tuple(group.deployments)[:_SHOWN_IDS],
        latest_bar_closes_at=latest,
    )


def _systemic(key: _Key, group: _Group) -> FleetSystemicBlockerPayload | None:
    """Flag evidence blocks, repeated sizing skips and reasons shared across bots."""
    outcome, code, skip = key
    bots = len(group.deployments)
    if outcome == DecisionOutcome.ENTRY_BLOCKED.value and code in EVIDENCE_REASON_CODES:
        kind: Literal["evidence_block", "shared_reason", "sizing_skips"] = "evidence_block"
        detail = (
            f"{code} blocked {group.rows} matched signal(s) on {bots} bot(s): evidence the "
            "gate needs is missing. Run fleet-health entries and repair the named record."
        )
    elif skip == DecisionSkipReason.ENTRY_SIZING.value and group.rows >= 2:
        kind = "sizing_skips"
        detail = (
            f"{code} skipped {group.rows} matched signal(s) on {bots} bot(s) at sizing; "
            "review min_quote_notional, allocation and risk sizing."
        )
    elif bots >= 2 and not _routine(skip):
        kind = "shared_reason"
        detail = f"{code} hit {bots} bots ({group.rows} bar(s)); one cause likely affects all."
    else:
        return None
    return FleetSystemicBlockerPayload(
        kind=kind,
        outcome=_outcome(outcome),
        reason_code=code,
        rows=group.rows,
        deployments=bots,
        deployment_ids=tuple(group.deployments)[:_SHOWN_IDS],
        detail=detail,
    )


def _routine(skip: str | None) -> bool:
    """True for a skip reason every healthy bot produces routinely."""
    return skip is not None and DecisionSkipReason(skip) in _ROUTINE_SKIPS


def _outcome(value: str) -> Literal["entry_blocked", "skipped"]:
    """Narrow a grouped outcome to the two the report reads."""
    return "entry_blocked" if value == DecisionOutcome.ENTRY_BLOCKED.value else "skipped"
