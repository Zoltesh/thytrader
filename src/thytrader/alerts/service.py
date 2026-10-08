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

import asyncio
from contextlib import suppress
from datetime import timedelta
import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.alerts.store import DELIVERY_DISABLED_DETAIL
from thytrader.config import NotifyProvider
from thytrader.execution.ids import utc_now
from thytrader.memory.models import ActorOrigin, DeliveryStatus, NotificationRecord, NotifySeverity
from thytrader.memory.notify import DeliveryResult

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from thytrader.alerts.models import AlertCheck, OperatorAlert, SupervisionFinding
    from thytrader.alerts.store import AlertApplication, AlertStore
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

    async def open_alerts(self) -> tuple[OperatorAlert, ...]:
        """Read complete prior evidence; a failed read must never certify recovery."""
        return await self._store.list_open_alerts()

    async def apply(
        self,
        findings: Sequence[SupervisionFinding],
        *,
        now: datetime,
        evaluated: Sequence[AlertCheck] = (),
        dispatch: bool = True,
    ) -> AlertApplication:
        """Atomically accept findings and resolve only proven re-evaluated checks.

        An empty/partial pass means unknown by default. Strictly newer durable
        check watermarks reject stale/replayed passes across service restarts.
        """
        ordered: dict[tuple[str, str], SupervisionFinding] = {}
        for finding in findings:
            ordered.setdefault((finding.code.value, finding.subject), finding)
        application = await self._store.apply_observations(
            tuple(ordered.values()), evaluated, now=now, detail=_RECOVERY_DETAIL
        )
        # The execution safety loop uses dispatch=False; a separate task handles I/O.
        if dispatch:
            await self.dispatch_pending(now=now)
        return application

    async def dispatch_pending(self, *, now: datetime) -> None:
        """Retry open alerts independently of market evidence and signal evaluation."""
        for alert in await self._store.list_open_alerts():
            await self.deliver(alert, now=now)

    async def run_deliveries(self, stop_requested: asyncio.Event) -> None:
        """Poll delivery in its own worker task; provider faults never delay safety cycles."""
        while not stop_requested.is_set():
            try:
                await self.dispatch_pending(now=utc_now())
            except Exception:  # noqa: BLE001 - optional delivery task cannot kill execution.
                _logger.warning("alert_dispatch_unavailable")
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_requested.wait(), timeout=30)

    async def deliver(self, alert: OperatorAlert, *, now: datetime) -> None:
        """CAS-claim before sending; never persist provider-controlled error text.

        Stable alert IDs let a webhook recipient dedupe a retry after an ambiguous
        send/ack crash. Exactly-once delivery requires that recipient cooperation.
        Cancellation leaves the durable claim to expire and consumes one attempt.
        """
        try:
            provider = self._sender.provider()
            if not isinstance(provider, NotifyProvider):
                _logger.warning("alert_provider_invalid")
                return
            claim = await self._store.claim_delivery(
                alert.id,
                provider=provider.value,
                now=now,
                max_attempts=self._thresholds.delivery_max_attempts,
                ttl=timedelta(seconds=60),
            )
            if claim is None:
                return
            record = _notification_record(claim.alert, now=now, provider=provider)
            try:
                result = await asyncio.wait_for(self._sender.deliver(record), timeout=15)
            except Exception:  # noqa: BLE001 - provider errors never escape or leak text.
                result = DeliveryResult(status=DeliveryStatus.FAILED)
            status = (
                result.status
                if isinstance(result.status, DeliveryStatus)
                else DeliveryStatus.FAILED
            )
            await self._store.finish_delivery(
                alert.id,
                claim.token,
                status=status.value,
                detail=(
                    DELIVERY_DISABLED_DETAIL
                    if status is DeliveryStatus.SKIPPED
                    else f"notification outcome: {status.value}"
                ),
            )
        except Exception:  # noqa: BLE001 - storage/provider callbacks cannot fail supervision.
            _logger.warning("alert_delivery_unavailable code=%s", alert.code.value)


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
        id=alert.id,
        occurred_at=now,
        origin=ActorOrigin.SYSTEM,
        title=title,
        body=body,
        severity=_SEVERITY_TO_NOTIFY[alert.severity.value],
        provider=provider,
        delivery_status=DeliveryStatus.SKIPPED,
        detail="",
    )
