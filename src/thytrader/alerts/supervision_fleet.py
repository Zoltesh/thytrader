"""Fleet entry block alerts from one fleet entry readiness evaluation (ADR 0130).

One ``FLEET_ENTRIES_BLOCKED`` alert per mode and quote scope (subject
``fleet:<mode>:<scope>``) is raised on the first evaluation that finds the entry gate
denying every new entry there for a reason that does not clear by itself, deduplicated while
it lasts, and resolved on the first complete evaluation that finds the scope admissible.
Self-clearing blocks (``capacity``: a fully invested fleet; ``transient``: the clustering
window) are reported but never alerted.

Severity is ``critical`` for live scopes blocked by missing evidence (the 2026-10-10 class
of failure, where nothing but a repair clears it) and ``warning`` otherwise. Unknown checks
prove nothing, so they never resolve an open alert.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.alerts.models import (
    AlertCheck,
    AlertCode,
    AlertScope,
    AlertSeverity,
    SafetyEvidence,
    SupervisionFinding,
)
from thytrader.risk.fleet_entry_models import scope_subject
from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.alerts.models import OperatorAlert
    from thytrader.risk.fleet_entry_models import (
        FleetEntryCheck,
        FleetEntryHealth,
        FleetScopeHealth,
    )

_BOOK_DETAIL = 220


def fleet_entry_evidence(
    health: FleetEntryHealth, *, prior_alerts: Sequence[OperatorAlert] = ()
) -> SafetyEvidence:
    """Findings for alertable blocked scopes and the checks that prove recovery.

    An incomplete evaluation (the fleet could not be listed) proves nothing. A scope that no
    longer exists in a complete evaluation has no occupied book left to block, so its open
    alert resolves.
    """
    if not health.complete:
        return SafetyEvidence()
    findings: list[SupervisionFinding] = []
    evaluated: list[AlertCheck] = []
    subjects: set[str] = set()
    for scope in health.scopes:
        subject = scope_subject(scope.mode, scope.scope)
        subjects.add(subject)
        finding = fleet_entry_finding(scope)
        if finding is not None:
            findings.append(finding)
        elif not any(check.status == "unknown" for check in scope.checks):
            evaluated.append(AlertCheck(AlertCode.FLEET_ENTRIES_BLOCKED, subject))
    evaluated.extend(
        AlertCheck(AlertCode.FLEET_ENTRIES_BLOCKED, row.subject)
        for row in prior_alerts
        if row.code is AlertCode.FLEET_ENTRIES_BLOCKED
        and row.is_open
        and row.subject not in subjects
    )
    return SafetyEvidence(tuple(findings), tuple(evaluated))


def fleet_entry_finding(scope: FleetScopeHealth) -> SupervisionFinding | None:
    """The alert finding for one scope, or ``None`` when nothing alertable blocks it."""
    alertable = scope.alertable
    if not alertable:
        return None
    critical = scope.mode is DeploymentMode.LIVE and any(
        check.blocker_class == "evidence" for check in alertable
    )
    first_book = next((book for check in alertable for book in check.books), None)
    return SupervisionFinding(
        code=AlertCode.FLEET_ENTRIES_BLOCKED,
        scope=AlertScope.FLEET,
        subject=scope_subject(scope.mode, scope.scope),
        severity=AlertSeverity.CRITICAL if critical else AlertSeverity.WARNING,
        detail=fleet_block_summary(scope, alertable),
        deployment_id=None if first_book is None else first_book.deployment_id,
    )


def fleet_block_summary(scope: FleetScopeHealth, checks: Sequence[FleetEntryCheck]) -> str:
    """One line naming the blocking checks, codes and the first responsible record."""
    label = f"{scope.mode.value.capitalize()} {scope.scope}"
    blocks = ", ".join(f"{check.name}={check.reason_code}" for check in checks)
    first = next(((check, book) for check in checks for book in check.books), None)
    if first is None:
        cause = checks[0].detail
    else:
        _check, book = first
        cause = f"deployment {book.deployment_id} ({book.status} {book.product_id}): {book.detail}"
    return (
        f"{label} entries are blocked fleet-wide ({blocks}). {cause[:_BOOK_DETAIL].rstrip('.')}. "
        "See thytrader-operator fleet-health."
    )
