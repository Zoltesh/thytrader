"""Grade external alert delivery against the live books it must cover (ADR 0138).

A durable local alert feed only helps an operator who polls it. While any live book holds
risk (running or paused), alerts that never leave the installation are a silent gap, so
disabled or log-only delivery is ``degraded`` rather than ``healthy``. Paper-only and idle
installations keep the deliberate default-off provider without a warning.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.config import NotifyProvider
from thytrader.operator.models import ComponentReport, ReportStatus
from thytrader.trading.models import DeploymentMode, DeploymentStatus

if TYPE_CHECKING:
    from thytrader.alerts.store import AlertStore
    from thytrader.config import Settings
    from thytrader.trading.store import ExecutionStore

ALERT_DELIVERY_COMPONENT = "alert_delivery"
_LIVE_RISK_STATUSES = frozenset({DeploymentStatus.RUNNING, DeploymentStatus.PAUSED})


async def live_risk_book_count(execution: ExecutionStore | None) -> int | None:
    """Count live running or paused books; ``None`` when the fleet cannot be read."""
    if execution is None:
        return None
    try:
        deployments = await execution.list_deployments()
    except Exception:  # noqa: BLE001 - an unreadable fleet is unknown, never "no live books".
        return None
    return sum(
        1
        for deployment in deployments
        if deployment.mode is DeploymentMode.LIVE and deployment.status in _LIVE_RISK_STATUSES
    )


async def open_alert_delivery_failed(store: AlertStore | None) -> bool:
    """Whether any open alert's latest delivery failed; unreadable storage is graded elsewhere."""
    if store is None:
        return False
    try:
        rows = await store.list_open_alerts()
    except Exception:  # noqa: BLE001 - the alerts feed component reports unavailable storage.
        return False
    return any(row.delivery_status == "failed" for row in rows)


def alert_delivery_component(
    settings: Settings, *, live_books: int | None, failed_delivery: bool
) -> ComponentReport:
    """Grade delivery: webhook failures, and no external delivery while live books hold risk."""
    provider = settings.notify_provider
    if provider is NotifyProvider.WEBHOOK:
        if failed_delivery:
            return ComponentReport(
                name=ALERT_DELIVERY_COMPONENT,
                status=ReportStatus.DEGRADED,
                reason_code="DELIVERY_FAILED",
                detail="An open alert could not be delivered. The local feed still has it.",
            )
        return ComponentReport(
            name=ALERT_DELIVERY_COMPONENT,
            status=ReportStatus.HEALTHY,
            reason_code="DELIVERY_CONFIGURED",
            detail="Webhook delivery is configured. Webhook URLs are not reported.",
        )
    if live_books is None:
        return ComponentReport(
            name=ALERT_DELIVERY_COMPONENT,
            status=ReportStatus.DEGRADED,
            reason_code="DELIVERY_DISABLED_LIVE_UNKNOWN",
            detail=(
                f"notify_provider={provider.value} sends no external alerts and the fleet could "
                "not be read, so live books may be running unwatched."
            ),
        )
    if live_books:
        return ComponentReport(
            name=ALERT_DELIVERY_COMPONENT,
            status=ReportStatus.DEGRADED,
            reason_code="DELIVERY_DISABLED_WHILE_LIVE",
            detail=(
                f"{live_books} live book(s) are running or paused but notify_provider="
                f"{provider.value} sends no external alerts. Configure a webhook."
            ),
        )
    return ComponentReport(
        name=ALERT_DELIVERY_COMPONENT,
        status=ReportStatus.HEALTHY,
        reason_code="DELIVERY_DISABLED",
        detail=(
            f"notify_provider={provider.value}: no external delivery, and no live book is "
            "running or paused. Alerts stay in the local durable feed."
        ),
    )
