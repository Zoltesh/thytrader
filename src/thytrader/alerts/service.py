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
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.alerts.models import AlertCode
from thytrader.alerts.store import DELIVERY_DISABLED_DETAIL
from thytrader.config import NotifyProvider
from thytrader.memory.models import ActorOrigin, DeliveryStatus, NotificationRecord, NotifySeverity
from thytrader.memory.notify import DeliveryResult
from thytrader.trading.ids import utc_now

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from thytrader.alerts.models import (
        AlertCheck,
        AlertSeverity,
        OperatorAlert,
        SupervisionFinding,
    )
    from thytrader.alerts.store import AlertApplication, AlertStore
    from thytrader.alerts.supervision_inputs import AlertThresholds

_logger = logging.getLogger(__name__)

_SEVERITY_TO_NOTIFY: dict[str, NotifySeverity] = {
    "info": NotifySeverity.INFO,
    "warning": NotifySeverity.WARNING,
    "critical": NotifySeverity.ERROR,
}
_RECOVERY_DETAIL = "condition cleared; next observation confirmed recovery"
_SEVERITY_RANK: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}
_TITLE_BOUND = 200
_SENT = frozenset({"logged", "delivered"})
# Resolved rows of one block read to join a return to its episode; the walk stops at the
# newest covering row, so only returns never seen by the delivery task count toward it.
_EPISODE_HISTORY = 500
_EPISODE_SKIP = "continues an entry-block episode already sent"
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
        min_severity: Callable[[], str] = lambda: "info",
    ) -> None:
        """Bind the store, sender, thresholds and the live minimum delivery severity.

        ``min_severity`` is read at each delivery so a YAML change applies without a
        restart (ADR 0139). Alerts below it are recorded as skipped, never sent.
        """
        self._store = store
        self._sender = sender
        self._thresholds = thresholds
        self._min_severity = min_severity

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
            minimum = self._min_severity()
            if provider is not NotifyProvider.NONE and _SEVERITY_RANK[
                alert.severity.value
            ] < _SEVERITY_RANK.get(minimum, 0):
                detail = f"below notify_min_severity={minimum}: alert retained locally"
                # Failed rows keep their evidence; already-skipped rows need no rewrite.
                if alert.delivery_status == "pending" or (
                    alert.delivery_status == "skipped" and alert.delivery_detail != detail
                ):
                    await self._store.skip_delivery(
                        alert.id, provider=provider.value, now=now, detail=detail
                    )
                return
            if (
                provider is not NotifyProvider.NONE
                and alert.code is AlertCode.FLEET_ENTRIES_BLOCKED
                and alert.delivery_status in {"pending", "skipped"}
                and not await self._entry_block_due(alert, provider=provider, now=now)
            ):
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
                covered_severity=claim.alert.severity if status.value in _SENT else None,
            )
        except Exception:  # noqa: BLE001 - storage/provider callbacks cannot fail supervision.
            _logger.warning("alert_delivery_unavailable code=%s", alert.code.value)

    async def _entry_block_due(
        self, alert: OperatorAlert, *, provider: NotifyProvider, now: datetime
    ) -> bool:
        """An entry block is sent once its episode outlasts the grace; a return is not resent."""
        grace = timedelta(seconds=self._thresholds.entry_block_grace_seconds)
        if grace <= timedelta(0):
            return True
        # One row past the window shows whether the episode reaches beyond it.
        history = await self._store.list_resolved(
            alert.code.value, alert.subject, limit=_EPISODE_HISTORY + 1
        )
        episode = _entry_block_episode(alert, history, grace=grace, now=now)
        if episode.sending:
            return False
        if episode.sent is not None:
            if alert.delivery_status == "skipped":
                return False
            await self._store.skip_delivery(
                alert.id,
                provider=provider.value,
                now=now,
                detail=f"{_EPISODE_SKIP} (see alert {episode.sent.id})",
                covered_severity=_covered(episode.sent),
            )
            return False
        return now - episode.started >= grace


@dataclass(frozen=True, slots=True)
class _Episode:
    """When an entry-block episode began, its covering sent row, and any send in flight."""

    started: datetime
    sent: OperatorAlert | None = None
    sending: bool = False


def _entry_block_episode(
    alert: OperatorAlert, history: Sequence[OperatorAlert], *, grace: timedelta, now: datetime
) -> _Episode:
    """Walk this block's resolved rows back to the first gap longer than the grace.

    A block that clears and returns within the grace continues the same episode, so a
    flapping block accumulates time toward a page and is paged once, not once per return.
    A row whose page covered this severity or higher (sent, or skipped as a continuation of
    such a page) covers the return; a lower-severity page does not, so an escalation is paged.
    Coverage is the severity actually sent, never the row's later observed severity. A send
    still in flight on an earlier row holds the return until it is acknowledged or its claim
    expires. Only ``_EPISODE_HISTORY`` rows are walked: when a row exists beyond them and the
    walk found no gap or covering row, the episode's start is older than the window, so it is
    due at once rather than letting the start slide forward (an early or duplicate page, never
    a missed one).
    """
    started = alert.first_seen_at
    rank = _rank(alert.severity)
    for row in history[:_EPISODE_HISTORY]:
        if row.id == alert.id or row.resolved_at is None or row.resolved_at > started:
            continue
        if started - row.resolved_at > grace:
            break
        started = min(started, row.first_seen_at)
        if row.delivery_expires_at is not None and row.delivery_expires_at > now:
            return _Episode(started, sending=True)
        covered = _covered(row)
        if covered is not None and _rank(covered) >= rank:
            return _Episode(started, sent=row)
    else:
        if len(history) > _EPISODE_HISTORY:
            return _Episode(started - grace)
    return _Episode(started)


def _covered(row: OperatorAlert) -> AlertSeverity | None:
    """The severity this row's page covered; a row sent before 0081 covers nothing.

    Never the row's observed severity, which supervision rewrites after a page: an unknown
    coverage can only cause a duplicate page, never a missed one.
    """
    return row.covered_severity


def _rank(severity: AlertSeverity) -> int:
    """Order info < warning < critical."""
    return _SEVERITY_RANK[severity.value]


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
