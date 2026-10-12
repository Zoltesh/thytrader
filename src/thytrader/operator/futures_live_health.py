"""Health projection of the durable futures safety feed; not permission to trade."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.alerts.models import AlertSeverity
from thytrader.alerts.store import AlertStoreError
from thytrader.operator.models import ComponentReport, ReportStatus

if TYPE_CHECKING:
    from thytrader.alerts.store import AlertStore


def _unavailable() -> ComponentReport:
    """Missing storage is explicitly unknown, never an empty healthy feed."""
    return ComponentReport(
        name="futures_live",
        status=ReportStatus.DEGRADED,
        reason_code="FUTURES_ALERTS_UNAVAILABLE",
        detail="Futures safety alerts cannot be read; this is unknown, not all-clear.",
    )


async def futures_live_health_component(store: AlertStore | None) -> ComponentReport:
    """Read every open futures alert, never a bounded display feed or venue endpoint."""
    if store is None:
        return _unavailable()
    try:
        rows = tuple(
            row for row in await store.list_open_alerts() if row.code.value.startswith("FUTURES_")
        )
    except AlertStoreError:
        return _unavailable()
    if not rows:
        return ComponentReport(
            name="futures_live",
            status=ReportStatus.HEALTHY,
            reason_code="OK",
            detail="No open futures safety alerts. This is not proof of a successful "
            "observation or permission to start live futures.",
        )
    critical = tuple(row for row in rows if row.severity is AlertSeverity.CRITICAL)
    first = (critical or rows)[0]
    return ComponentReport(
        name="futures_live",
        status=ReportStatus.FAILED if critical else ReportStatus.DEGRADED,
        reason_code=first.code.value,
        detail=f"{len(rows)} open futures safety alert(s). {first.subject}: {first.detail}"[:500],
    )
