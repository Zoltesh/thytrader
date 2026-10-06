"""Dedupe, resolve, and deliver durable operator alerts (ADR 0115).

The service owns three rules the supervisor relies on:

* one open alert row per ``(code, subject)`` — repeated cycles increment the
  occurrence counter instead of flooding a new row;
* recovery resolves the open row exactly once, and a later recurrence opens a
  new row (append-oriented history);
* delivery through the configured notification provider is best effort: with
  ``notify_provider=none`` the row records an explicit skipped delivery, and a
  failing provider retries only while the alert stays open and the attempt
  budget is unspent. Delivery failures never fail the worker cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.alerts.store import DELIVERY_DISABLED_DETAIL, AlertChange
from thytrader.memory.models import (
    ActorOrigin,
    DeliveryStatus,
    NotificationRecord,
    NotifyProvider,
    NotifySeverity,
)
from thytrader.memory.notify import DeliveryResult

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from thytrader.alerts.models import OperatorAlert, SupervisionFinding
    from thytrader.alerts.store import AlertStore
    from thytrader.alerts.supervision import AlertThresholds

_logger = logging.getLogger(__name__)

_SEVERITY_TO_NOTIFY: dict[str, NotifySeverity] = {
    "info": NotifySeverity.INFO,
    "warning": NotifySeverity.WARNING,
    "critical": NotifySeverity.ERROR,
}
_RECOVERY_DETAIL = "condition cleared; next observation confirmed recovery"
_TITLE_BOUND = 200
_BODY_BOUND = 4000


@runtime_checkable
class AlertDeliverySender(Protocol):
    """The shipped notification sender contract used for alert dispatch."""

    def provider(self) -> NotifyProvider:
        """Return the configured provider identity."""
        ...

    async def deliver(self, record: NotificationRecord) -> DeliveryResult:
        """Attempt delivery without echoing webhook URLs or secrets."""
        ...


@dataclass(frozen=True, slots=True)
class AlertApplication:
    """What one ``apply`` pass recorded, resolved, and tried to deliver."""

    changes: tuple[AlertChange, ...]
    resolved_count: int


class AlertService:
    """Apply supervision findings to a durable alert store with delivery."""

    def __init__(
        self,
        store: AlertStore,
        sender: AlertDeliverySender,
        *,
        thresholds: AlertThresholds,
    ) -> None:
        """Bind the durable store, the delivery sender, and the thresholds."""
        self._store = store
        self._sender = sender
        self._thresholds = thresholds

    @property
    def thresholds(self) -> AlertThresholds:
        """The supervision thresholds this service was bound with."""
        return self._thresholds

    async def apply(
        self, findings: Sequence[SupervisionFinding], *, now: datetime
    ) -> AlertApplication:
        """Record findings, resolve recovered alerts, then dispatch deliveries.

        Findings are deduplicated by ``(code, subject)`` before touching the
        store so one cycle can never double-record the same issue.
        """
        ordered: dict[tuple[str, str], SupervisionFinding] = {}
        for finding in findings:
            ordered.setdefault((finding.code.value, finding.subject), finding)
        changes = [await self._store.record(finding, now=now) for finding in ordered.values()]
        resolved = await self._store.resolve_absent(
            tuple((finding.code, finding.subject) for finding in ordered.values()),
            now=now,
            detail=_RECOVERY_DETAIL,
        )
        for change in changes:
            if _needs_delivery(change.alert, self._thresholds.delivery_max_attempts):
                await self.deliver(change.alert, now=now)
        return AlertApplication(changes=tuple(changes), resolved_count=resolved)

    async def deliver(self, alert: OperatorAlert, *, now: datetime) -> None:
        """Attempt one delivery and persist the outcome; never raises."""
        provider = self._sender.provider()
        record = _notification_record(alert, now=now, provider=provider)
        try:
            result = await self._sender.deliver(record)
        except Exception as error:  # noqa: BLE001 - delivery must not fail the cycle.
            _logger.warning(
                "alert_delivery_error code=%s type=%s", alert.code.value, type(error).__name__
            )
            result = DeliveryResult(
                status=DeliveryStatus.FAILED, detail=f"sender error {type(error).__name__}"
            )
        detail = result.detail or ""
        if result.status is DeliveryStatus.SKIPPED:
            detail = DELIVERY_DISABLED_DETAIL
        try:
            await self._store.record_delivery(
                alert.id,
                provider=provider.value,
                status=result.status.value,
                detail=detail,
                attempted_at=now,
            )
        except Exception as error:  # noqa: BLE001 - bookkeeping must not fail the cycle.
            _logger.warning(
                "alert_delivery_bookkeeping_failed code=%s type=%s",
                alert.code.value,
                type(error).__name__,
            )


def _needs_delivery(alert: OperatorAlert, max_attempts: int) -> bool:
    """True when an open alert still owes a first delivery or a bounded retry."""
    if not alert.is_open:
        return False
    if alert.delivery_status == "pending":
        return True
    return alert.delivery_status == "failed" and alert.delivery_attempts < max_attempts


def _notification_record(
    alert: OperatorAlert, *, now: datetime, provider: NotifyProvider
) -> NotificationRecord:
    """Build the provider payload without secrets, balances, or webhook URLs."""
    title = f"[thytrader] {alert.severity.value.upper()}: {alert.code.value}"[:_TITLE_BOUND]
    body = (
        f"{alert.detail}\nsubject={alert.subject} "
        f"first_seen={alert.first_seen_at.isoformat()} occurrences={alert.occurrences}"
    )[:_BODY_BOUND]
    return NotificationRecord(
        occurred_at=now,
        origin=ActorOrigin.SYSTEM,
        title=title,
        body=body,
        severity=_SEVERITY_TO_NOTIFY[alert.severity.value],
        provider=provider,
        delivery_status=DeliveryStatus.SKIPPED,
        detail="",
    )
