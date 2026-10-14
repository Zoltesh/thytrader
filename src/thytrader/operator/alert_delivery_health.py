"""Grade external alert delivery against the live books it must cover (ADR 0138).

A durable local alert feed only helps an operator who polls it. While any live book holds
risk (running, paused, or stopped with residual inventory or working orders under managed
shutdown), alerts that never leave the installation are a silent gap, so disabled or log-only
delivery is ``degraded`` rather than ``healthy``. Paper-only and idle installations keep the
deliberate default-off provider without a warning.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.config import NotifyProvider
from thytrader.operator.models import ComponentReport, ReportStatus
from thytrader.trading.exposure import snapshot_has_residual_exposure
from thytrader.trading.models import DeploymentMode, DeploymentStatus
from thytrader.trading.store import DeploymentSnapshotBatchReader

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.alerts.store import AlertStore
    from thytrader.config import Settings
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore

ALERT_DELIVERY_COMPONENT = "alert_delivery"
_LIVE_RISK_STATUSES = frozenset({DeploymentStatus.RUNNING, DeploymentStatus.PAUSED})


async def live_risk_book_count(execution: ExecutionStore | None) -> int | None:
    """Count live books holding risk; ``None`` when the fleet cannot be read.

    Running and paused live books count. Stopped live books count while they still carry a
    position, a working entry or an in-market phase (managed shutdown keeps protection), or a
    pending futures de-risk duty.
    """
    if execution is None:
        return None
    try:
        deployments = await execution.list_deployments()
        live = tuple(item for item in deployments if item.mode is DeploymentMode.LIVE)
        stopped = tuple(item.id for item in live if item.status is DeploymentStatus.STOPPED)
        snapshots = await _snapshots(execution, stopped)
    except Exception:  # noqa: BLE001 - an unreadable fleet is unknown, never "no live books".
        return None
    active = sum(1 for item in live if item.status in _LIVE_RISK_STATUSES)
    residual = sum(
        1
        for snapshot in snapshots
        if snapshot.deployment.futures_derisk_pending or snapshot_has_residual_exposure(snapshot)
    )
    return active + residual


async def _snapshots(
    execution: ExecutionStore, ids: tuple[UUID, ...]
) -> tuple[DeploymentSnapshot, ...]:
    """Load stopped live books in one read when the store batches, else one by one."""
    if not ids:
        return ()
    if isinstance(execution, DeploymentSnapshotBatchReader):
        return await execution.get_deployments(ids)
    return tuple([await execution.get_deployment(item) for item in ids])


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
                f"{live_books} live book(s) hold risk (running, paused, or stopped with "
                f"residual exposure) but notify_provider="
                f"{provider.value} sends no external alerts. Configure a webhook."
            ),
        )
    return ComponentReport(
        name=ALERT_DELIVERY_COMPONENT,
        status=ReportStatus.HEALTHY,
        reason_code="DELIVERY_DISABLED",
        detail=(
            f"notify_provider={provider.value}: no external delivery, and no live book holds "
            "risk. Alerts stay in the local durable feed."
        ),
    )
