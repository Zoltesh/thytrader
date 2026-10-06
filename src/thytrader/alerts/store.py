"""Atomic evidence-scoped alert persistence and delivery claims (ADR 0115)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import uuid4

from thytrader.alerts.models import AlertCheck, OperatorAlert, clip_alert_text, new_alert

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from thytrader.alerts.models import SupervisionFinding

ALERT_REPORT_ROW_LIMIT = 200
DELIVERY_DISABLED_DETAIL = "notify_provider=none: delivery disabled; alert retained locally"


class AlertStoreError(RuntimeError):
    """Signal that durable alert storage is unavailable."""


@dataclass(frozen=True, slots=True)
class AlertChange:
    """The accepted row and whether this observation opened it."""

    alert: OperatorAlert
    created: bool


@dataclass(frozen=True, slots=True)
class AlertApplication:
    """Accepted observations and the count of evidence-confirmed resolutions."""

    changes: tuple[AlertChange, ...]
    resolved_count: int


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    """Durable pre-send claim; only this token may acknowledge the attempt."""

    alert: OperatorAlert
    token: UUID


@runtime_checkable
class AlertStore(Protocol):
    """Apply complete check evidence atomically and persist pre-send claims.

    A persistent check watermark rejects stale/replayed observations, including
    failures arriving after a newer verified recovery. Missing checks do nothing.
    """

    async def record(self, finding: SupervisionFinding, *, now: datetime) -> AlertChange:
        """Record one positive observation (convenience for seeding callers)."""
        ...

    async def apply_observations(
        self,
        findings: Sequence[SupervisionFinding],
        evaluated: Sequence[AlertCheck],
        *,
        now: datetime,
        detail: str,
    ) -> AlertApplication:
        """Atomically record findings and resolve only explicitly evaluated keys."""
        ...

    async def list_open_alerts(self) -> tuple[OperatorAlert, ...]:
        """Read the complete open inventory, never a truncated report page."""
        ...

    async def list_alerts(self, *, limit: int) -> tuple[OperatorAlert, ...]:
        """Return a bounded operator feed, open first then recent resolutions."""
        ...

    async def claim_delivery(
        self,
        alert_id: UUID,
        *,
        provider: str,
        now: datetime,
        max_attempts: int,
        ttl: timedelta,
    ) -> DeliveryClaim | None:
        """Claim a bounded attempt before sending, or persist explicit disablement."""
        ...

    async def finish_delivery(
        self, alert_id: UUID, token: UUID, *, status: str, detail: str
    ) -> None:
        """Acknowledge only the currently owned claim, without changing evidence age."""
        ...


def observation_keys(
    findings: Sequence[SupervisionFinding], evaluated: Sequence[AlertCheck]
) -> tuple[AlertCheck, ...]:
    """Sort unique checks so transactional writers take locks in the same order."""
    keys = set(evaluated) | {AlertCheck(item.code, item.subject) for item in findings}
    return tuple(sorted(keys, key=lambda key: (key.code.value, key.subject)))


class InMemoryAlertStore:
    """Atomic event-loop test store with persistent-in-store check watermarks."""

    def __init__(self) -> None:
        """Start empty; rebuilding the service never resets these watermarks."""
        self.history: dict[tuple[str, str], list[OperatorAlert]] = {}
        self.checks: dict[AlertCheck, tuple[datetime, bool]] = {}

    def _open(self, identity: tuple[str, str]) -> OperatorAlert | None:
        """Return the unresolved row for one identity."""
        return next((row for row in reversed(self.history.get(identity, ())) if row.is_open), None)

    async def record(self, finding: SupervisionFinding, *, now: datetime) -> AlertChange:
        """Record one positive observation or return its already persisted row."""
        application = await self.apply_observations((finding,), (), now=now, detail="")
        if application.changes:
            return application.changes[0]
        existing = self._open((finding.code.value, finding.subject))
        if existing is None:
            raise AlertStoreError("Stale observation has no open alert.")
        return AlertChange(existing, False)

    async def apply_observations(
        self,
        findings: Sequence[SupervisionFinding],
        evaluated: Sequence[AlertCheck],
        *,
        now: datetime,
        detail: str,
    ) -> AlertApplication:
        """Apply without awaits between mutations, matching a single SQL transaction."""
        positive = {AlertCheck(item.code, item.subject): item for item in findings}
        changes: list[AlertChange] = []
        resolved = 0
        for check in observation_keys(findings, evaluated):
            previous = self.checks.get(check)
            failed = check in positive
            if previous is not None and (
                previous[0] > now or (previous[0] == now and (previous[1] or not failed))
            ):
                continue
            self.checks[check] = (now, failed)
            identity = (check.code.value, check.subject)
            row = self._open(identity)
            finding = positive.get(check)
            if finding is not None:
                refreshed = (
                    new_alert(finding, now=now)
                    if row is None
                    else replace(
                        row,
                        severity=finding.severity,
                        detail=clip_alert_text(finding.detail),
                        last_seen_at=now,
                        occurrences=row.occurrences + int(finding.count_occurrence),
                    )
                )
                if row is None:
                    self.history.setdefault(identity, []).append(refreshed)
                else:
                    self.history[identity][-1] = refreshed
                changes.append(AlertChange(refreshed, row is None))
            elif row is not None and row.last_seen_at < now:
                self.history[identity][-1] = replace(
                    row, resolved_at=now, resolution_detail=clip_alert_text(detail)
                )
                resolved += 1
        return AlertApplication(tuple(changes), resolved)

    async def list_open_alerts(self) -> tuple[OperatorAlert, ...]:
        """Return the complete open inventory for supervision, not the UI limit."""
        return tuple(row for rows in self.history.values() for row in rows if row.is_open)

    async def list_alerts(self, *, limit: int) -> tuple[OperatorAlert, ...]:
        """Return open rows first, then resolutions by actual recovery time."""
        rows = [row for rows in self.history.values() for row in rows]
        return _ordered_for_report(rows)[:limit]

    async def claim_delivery(
        self,
        alert_id: UUID,
        *,
        provider: str,
        now: datetime,
        max_attempts: int,
        ttl: timedelta,
    ) -> DeliveryClaim | None:
        """Consume a retry slot atomically before the sender can make a side effect."""
        for rows in self.history.values():
            row = rows[-1]
            if row.id != alert_id or not row.is_open:
                continue
            if row.delivery_status in {"logged", "delivered"}:
                return None
            if provider == "none":
                rows[-1] = replace(
                    row,
                    delivery_provider=provider,
                    delivery_status="skipped",
                    delivery_detail=DELIVERY_DISABLED_DETAIL,
                    delivery_token=None,
                    delivery_expires_at=None,
                )
                return None
            if row.delivery_attempts >= max_attempts or (
                row.delivery_expires_at is not None and row.delivery_expires_at > now
            ):
                return None
            token = uuid4()
            claimed = replace(
                row,
                delivery_provider=provider,
                delivery_status="failed",
                delivery_detail="delivery attempt awaiting acknowledgement",
                delivery_attempts=row.delivery_attempts + 1,
                delivery_token=token,
                delivery_expires_at=now + ttl,
            )
            rows[-1] = claimed
            return DeliveryClaim(claimed, token)
        return None

    async def finish_delivery(
        self, alert_id: UUID, token: UUID, *, status: str, detail: str
    ) -> None:
        """Ignore delayed callbacks that lost their claim or whose alert resolved."""
        for rows in self.history.values():
            row = rows[-1]
            if row.id == alert_id and row.is_open and row.delivery_token == token:
                rows[-1] = replace(
                    row,
                    delivery_status=status,
                    delivery_detail=clip_alert_text(detail),
                    delivery_token=None,
                    delivery_expires_at=None,
                )


def _ordered_for_report(rows: Sequence[OperatorAlert]) -> tuple[OperatorAlert, ...]:
    """Open rows first by newest observation, then most recently resolved."""
    open_rows = sorted(
        (row for row in rows if row.is_open), key=lambda row: row.last_seen_at, reverse=True
    )
    resolved = sorted(
        (row for row in rows if not row.is_open),
        key=lambda row: row.resolved_at or row.last_seen_at,
        reverse=True,
    )
    return (*open_rows, *resolved)


class DisabledAlertStore(InMemoryAlertStore):
    """Unconfigured storage is never silently treated as durable supervision."""

    async def apply_observations(
        self,
        findings: Sequence[SupervisionFinding],
        evaluated: Sequence[AlertCheck],
        *,
        now: datetime,
        detail: str,
    ) -> AlertApplication:
        """Refuse observation writes without PostgreSQL."""
        del findings, evaluated, now, detail
        raise AlertStoreError("Alert storage is unavailable.")

    async def list_open_alerts(self) -> tuple[OperatorAlert, ...]:
        """Refuse to certify an empty open-alert inventory."""
        raise AlertStoreError("Alert storage is unavailable.")
