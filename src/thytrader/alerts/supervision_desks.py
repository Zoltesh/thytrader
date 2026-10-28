"""Agent desk wake alerts from one read of the desks and their newest wakes (ADR 0147, 0115).

A desk's model is asked only when its timer runs ``thytrader-desk wake`` and the harness
answers. Both can fail silently: a harness that keeps failing (an expired login, no credits, a
timeout) journals ``error`` or ``invalid`` wakes and never trades, and a timer that stopped
journals nothing at all. Each is one alert per desk (subject ``desk:<desk_id>``):

- ``DESK_WAKES_FAILING``: the newest :data:`FAILED_WAKES` wakes (journal order) all failed, or
  were refused because the answering runner is not the desk's (``DESK_CONFIG_CHANGED``,
  ``DESK_LIVE_IDENTITY_MISMATCH``), which repeats until an operator acts;
- ``DESK_WAKES_STALLED``: no wake (a skipped wake counts) for :data:`STALL_CADENCES` cadences
  plus :data:`STALL_GRACE`, counted from the newest wake's journal time (server-set
  ``finished_at``) or from when the desk became due to wake (``due_since``: created, resumed or
  given a new cadence), never from bookkeeping writes such as a settlement or day roll.

The failing alert names the failure by its code only (for example ``DESK_HARNESS_EXIT``), never
the harness output, which can carry anything the harness printed.

Both are ``critical`` for a live desk (real money is not being supervised by its model) and
``warning`` for a paper desk. A paused or retired desk is not expected to wake, so its alerts
resolve. A desk whose wakes could not be read proves nothing; an open alert on a desk no longer
listed resolves once the desk list itself was read. Alerts only record and warn: nothing here
pauses a desk or changes a book.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from thytrader.agent_desk.models import DeskStatus, WakeOutcome
from thytrader.alerts.models import (
    AlertCheck,
    AlertCode,
    AlertScope,
    AlertSeverity,
    SafetyEvidence,
    SupervisionFinding,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from thytrader.agent_desk.models import Desk, DeskWake
    from thytrader.alerts.models import OperatorAlert

FAILED_WAKES = 3
STALL_CADENCES = 3
STALL_GRACE = timedelta(minutes=10)
_FAILED = frozenset({WakeOutcome.ERROR, WakeOutcome.INVALID})
_CODES = (AlertCode.DESK_WAKES_FAILING, AlertCode.DESK_WAKES_STALLED)
# Refusals that repeat until an operator acts: the answering runner is not the desk's.
_IDENTITY_REFUSALS = frozenset({"DESK_CONFIG_CHANGED", "DESK_LIVE_IDENTITY_MISMATCH"})
_KNOWN_CODES = frozenset(
    {
        "refused",
        "DESK_CONFIG_CHANGED",
        "DESK_LIVE_IDENTITY_MISMATCH",
        "error",
        "invalid",
        "DESK_HARNESS_FAILED",
        "DESK_DECISION_INVALID",
        "DESK_HARNESS_EMPTY",
        "DESK_HARNESS_EXIT",
        "DESK_HARNESS_INVALID",
        "DESK_HARNESS_NOT_FOUND",
        "DESK_HARNESS_TIMEOUT",
        "DESK_HARNESS_UNKNOWN",
        "DESK_LIVE_OVERRIDE_REFUSED",
    }
)


def desk_subject(desk_id: str) -> str:
    """The alert subject of one desk."""
    return f"desk:{desk_id}"


def desk_wake_evidence(
    desks: Sequence[Desk],
    wakes: Mapping[str, Sequence[DeskWake] | None],
    *,
    now: datetime,
    prior_alerts: Sequence[OperatorAlert] = (),
) -> SafetyEvidence:
    """Findings for failing or stalled active desks and the checks that prove recovery.

    ``wakes`` maps each desk id to its newest wakes (newest first), or ``None`` when the read
    failed. ``desks`` must be the complete desk list.
    """
    findings: list[SupervisionFinding] = []
    evaluated: list[AlertCheck] = []
    listed: set[str] = set()
    for desk in desks:
        subject = desk_subject(desk.desk_id)
        listed.add(subject)
        if desk.status is not DeskStatus.ACTIVE:
            evaluated.extend(AlertCheck(code, subject) for code in _CODES)
            continue
        recent = wakes.get(desk.desk_id)
        if recent is None:
            continue
        try:
            judged = (
                (AlertCode.DESK_WAKES_FAILING, _failing(desk, recent)),
                (AlertCode.DESK_WAKES_STALLED, _stalled(desk, recent, now=now)),
            )
        except TypeError, ValueError:
            # One unreadable desk proves nothing and never stops the others being judged.
            continue
        for code, finding in judged:
            if finding is None:
                evaluated.append(AlertCheck(code, subject))
            else:
                findings.append(finding)
    evaluated.extend(
        AlertCheck(row.code, row.subject)
        for row in prior_alerts
        if row.code in _CODES and row.is_open and row.subject not in listed
    )
    return SafetyEvidence(tuple(findings), tuple(evaluated))


def _failing(desk: Desk, recent: Sequence[DeskWake]) -> SupervisionFinding | None:
    """The newest wakes all failed."""
    run = 0
    for wake in recent:
        refused = wake.outcome is WakeOutcome.REFUSED and wake.outcome_code in _IDENTITY_REFUSALS
        if wake.outcome not in _FAILED and not refused:
            break
        run += 1
    if run < FAILED_WAKES:
        return None
    return _finding(
        AlertCode.DESK_WAKES_FAILING,
        desk,
        f"its last {run} wakes failed, so its model is not trading ({_failure_code(recent[0])}). "
        "Check the harness login, credits, timeout and the timer's flags with `thytrader-desk "
        "journal`.",
    )


def _failure_code(wake: DeskWake) -> str:
    """The failure's known codes only: the outcome code and a known leading code of the detail.

    ``detail`` can carry whatever a runner posted, so only exact known codes are copied.
    """
    head = wake.detail.split(":", 1)[0].strip()
    codes = [wake.outcome_code or wake.outcome.value, *([head] if head in _KNOWN_CODES else [])]
    return ", ".join(code for code in dict.fromkeys(codes) if code in _KNOWN_CODES)


def _stalled(desk: Desk, recent: Sequence[DeskWake], *, now: datetime) -> SupervisionFinding | None:
    """No wake for several cadences since the newest wake or the desk's last change."""
    due = desk.due_since or desk.created_at
    since = max(recent[0].finished_at, due) if recent else due
    window = timedelta(minutes=STALL_CADENCES * desk.cadence_minutes) + STALL_GRACE
    if now - since <= window:
        return None
    minutes = int((now - since).total_seconds() // 60)
    return _finding(
        AlertCode.DESK_WAKES_STALLED,
        desk,
        f"no wake for {minutes} minutes on a {desk.cadence_minutes}-minute cadence; its timer "
        "may have stopped. Check `systemctl --user list-timers` or `thytrader-desk schedule`.",
    )


def _finding(code: AlertCode, desk: Desk, text: str) -> SupervisionFinding:
    """One desk finding: critical for a live desk, a warning for a paper one."""
    live = desk.mode == "live"
    return SupervisionFinding(
        code=code,
        scope=AlertScope.DESK,
        subject=desk_subject(desk.desk_id),
        severity=AlertSeverity.CRITICAL if live else AlertSeverity.WARNING,
        detail=f"{'Live' if live else 'Paper'} desk {desk.name}: {text}",
    )
