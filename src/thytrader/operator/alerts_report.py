"""Read-only operator report for the durable safety-alert feed (ADR 0115)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import Field

from thytrader import __version__
from thytrader.alerts.store import ALERT_REPORT_ROW_LIMIT, DisabledAlertStore, page_attempts
from thytrader.config import NotifyProvider
from thytrader.operator.alert_delivery_health import alert_delivery_component
from thytrader.operator.models import (
    STANDARD_REDACTION,
    ComponentReport,
    OperatorEnvelope,
    ReportStatus,
    _FrozenModel,
)
from thytrader.operator.status import aggregate_status, recommend_next_action

if TYPE_CHECKING:
    from thytrader.alerts.models import OperatorAlert
    from thytrader.alerts.store import AlertStore
    from thytrader.config import Settings

_DELIVERY_DISABLED = (
    "notify_provider=none: external delivery is disabled. Alerts stay in the local "
    "durable feed and are not sent to a webhook."
)
_RESOLVED_PAGE = 20


class AlertDeliveryView(_FrozenModel):
    """Delivery bookkeeping for one alert, without webhook URLs or secrets."""

    provider: str = Field(min_length=1, max_length=16)
    status: str = Field(min_length=1, max_length=16)
    attempts: int = Field(ge=0)
    detail: str = Field(default="", max_length=500)


class AlertItem(_FrozenModel):
    """One durable alert row as operators and agents may read it."""

    id: UUID
    code: str = Field(min_length=1, max_length=48)
    severity: Literal["info", "warning", "critical"]
    scope: Literal["deployment", "worker", "fleet"]
    subject: str = Field(min_length=1, max_length=128)
    deployment_id: UUID | None = None
    product_id: str | None = Field(default=None, max_length=32)
    detail: str = Field(max_length=500)
    first_seen_at: datetime
    last_seen_at: datetime
    occurrences: int = Field(ge=1)
    resolved_at: datetime | None = None
    delivery: AlertDeliveryView


class AlertsPayload(_FrozenModel):
    """Open alerts, recent resolutions, and whether external delivery is configured."""

    storage: Literal["available", "unavailable"]
    delivery_enabled: bool
    delivery_warning: str | None = Field(default=None, max_length=500)
    open_alerts: tuple[AlertItem, ...]
    resolved_alerts: tuple[AlertItem, ...]
    open_total: int = Field(ge=0)
    open_critical: int = Field(ge=0)
    open_warning: int = Field(ge=0)


class AlertsReport(OperatorEnvelope):
    """Read-only durable safety alerts. This report cannot place or cancel orders."""

    report_kind: Literal["alerts"] = "alerts"
    payload: AlertsPayload


async def build_alerts_report(
    store: AlertStore | None, settings: Settings, *, live_books: int | None
) -> AlertsReport:
    """Assemble the alerts envelope from the durable feed and notify settings.

    ``live_books`` counts live running or paused books (``None`` when unreadable): with no
    external delivery they make ``alert_delivery`` degraded (ADR 0138).
    """
    now = datetime.now(UTC)
    delivery_enabled = settings.notify_provider is not NotifyProvider.NONE
    warning = None if delivery_enabled else _DELIVERY_DISABLED
    rows, storage = await _rows(store)
    open_rows = tuple(
        sorted(
            (row for row in rows if row.is_open),
            key=lambda row: (row.severity.value == "critical", row.last_seen_at),
            reverse=True,
        )
    )
    resolved = tuple(row for row in rows if not row.is_open)[:_RESOLVED_PAGE]
    max_attempts = settings.alert_delivery_max_attempts
    open_critical = sum(1 for row in open_rows if row.severity.value == "critical")
    open_warning = sum(1 for row in open_rows if row.severity.value == "warning")
    failed_delivery = any(row.delivery_status == "failed" for row in open_rows)
    components = [
        _feed_component(storage=storage, open_critical=open_critical, open_warning=open_warning),
        alert_delivery_component(
            settings, live_books=live_books, failed_delivery=failed_delivery and delivery_enabled
        ),
    ]
    warnings: list[str] = []
    if warning is not None:
        warnings.append(warning)
    if storage == "unavailable":
        warnings.append("Alert storage is unavailable; the feed cannot be read.")
    if len(open_rows) > ALERT_REPORT_ROW_LIMIT:
        warnings.append(
            "Open alert display is bounded; totals/status use the complete open inventory."
        )
    return AlertsReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=tuple(components),
        redaction=STANDARD_REDACTION,
        partial_result_warnings=tuple(warnings),
        recommended_next_action=recommend_next_action(components),
        payload=AlertsPayload(
            storage=storage,
            delivery_enabled=delivery_enabled,
            delivery_warning=warning,
            open_alerts=tuple(
                _item(row, max_attempts=max_attempts) for row in open_rows[:ALERT_REPORT_ROW_LIMIT]
            ),
            resolved_alerts=tuple(_item(row, max_attempts=max_attempts) for row in resolved),
            open_total=len(open_rows),
            open_critical=open_critical,
            open_warning=open_warning,
        ),
    )


async def _rows(
    store: AlertStore | None,
) -> tuple[tuple[OperatorAlert, ...], Literal["available", "unavailable"]]:
    """Read the bounded feed, or report storage unavailable without inventing rows."""
    if store is None or isinstance(store, DisabledAlertStore):
        return (), "unavailable"
    try:
        open_rows = await store.list_open_alerts()
        recent = await store.list_alerts(limit=ALERT_REPORT_ROW_LIMIT)
        return (*open_rows, *(row for row in recent if not row.is_open)), "available"
    except Exception:  # noqa: BLE001 - a read failure is an unavailable feed, not an empty one.
        return (), "unavailable"


def _feed_component(*, storage: str, open_critical: int, open_warning: int) -> ComponentReport:
    """Grade the durable feed: storage availability, then open critical/warning rows."""
    if storage != "available":
        return ComponentReport(
            name="alerts",
            status=ReportStatus.DEGRADED,
            reason_code="ALERT_STORAGE_UNAVAILABLE",
            detail="Durable alert storage is not configured.",
        )
    if open_critical:
        return ComponentReport(
            name="alerts",
            status=ReportStatus.FAILED,
            reason_code="OPEN_CRITICAL_ALERTS",
            detail=f"{open_critical} open critical safety alert(s).",
        )
    if open_warning:
        return ComponentReport(
            name="alerts",
            status=ReportStatus.DEGRADED,
            reason_code="OPEN_WARNING_ALERTS",
            detail=f"{open_warning} open warning safety alert(s).",
        )
    return ComponentReport(
        name="alerts",
        status=ReportStatus.HEALTHY,
        reason_code="NO_OPEN_ALERTS",
        detail="No open safety alerts.",
    )


def _item(alert: OperatorAlert, *, max_attempts: int) -> AlertItem:
    """Project one stored row onto the strict report item."""
    status = alert.delivery_status or "pending"
    # Each severity's page has its own budget (ADR 0115); ``attempts`` stays the row's total.
    if status == "failed" and page_attempts(alert) >= max_attempts:
        status = "exhausted"
    return AlertItem(
        id=alert.id,
        code=alert.code.value,
        severity=_severity(alert.severity.value),
        scope=_scope(alert.scope.value),
        subject=alert.subject,
        deployment_id=alert.deployment_id,
        product_id=alert.product_id,
        detail=alert.detail,
        first_seen_at=alert.first_seen_at,
        last_seen_at=alert.last_seen_at,
        occurrences=alert.occurrences,
        resolved_at=alert.resolved_at,
        delivery=AlertDeliveryView(
            provider=alert.delivery_provider or "none",
            status=status or "pending",
            attempts=alert.delivery_attempts,
            detail=alert.delivery_detail,
        ),
    )


def _severity(value: str) -> Literal["info", "warning", "critical"]:
    """Narrow a stored severity onto the report literal."""
    if value == "critical":
        return "critical"
    if value == "warning":
        return "warning"
    return "info"


def _scope(value: str) -> Literal["deployment", "worker", "fleet"]:
    """Narrow a stored scope onto the report literal."""
    if value == "worker":
        return "worker"
    if value == "fleet":
        return "fleet"
    return "deployment"
