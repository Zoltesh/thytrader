"""Alert persistence contracts plus in-memory and disabled implementations (ADR 0115)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.alerts.models import OperatorAlert, clip_alert_text, new_alert

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from thytrader.alerts.models import AlertCode, SupervisionFinding

#: How many alert rows a report read returns (open rows first, then recent
#: resolutions). Bounded so the operator feed stays a feed, not an archive.
ALERT_REPORT_ROW_LIMIT = 200

#: Delivery detail recorded when the operator has not configured a provider.
DELIVERY_DISABLED_DETAIL = "notify_provider=none: delivery disabled; alert retained locally"


class AlertStoreError(RuntimeError):
    """Signal that durable alert storage is unavailable."""


@dataclass(frozen=True, slots=True)
class AlertChange:
    """The row a ``record`` call produced and whether this cycle opened it."""

    alert: OperatorAlert
    created: bool


@runtime_checkable
class AlertStore(Protocol):
    """Persist deduplicated alerts and their delivery state.

    One open row exists per ``(code, subject)``. Recording an already-open issue
    increments its occurrence counter and refreshes ``last_seen_at``; recording
    after a resolution opens a new row. Resolution is idempotent.
    """

    async def record(self, finding: SupervisionFinding, *, now: datetime) -> AlertChange:
        """Insert or refresh the open alert row for one finding."""
        ...

    async def resolve_absent(
        self, present: Sequence[tuple[AlertCode, str]], *, now: datetime, detail: str
    ) -> int:
        """Resolve every open alert whose identity is not in ``present``."""
        ...

    async def list_alerts(self, *, limit: int) -> tuple[OperatorAlert, ...]:
        """Return open rows first (newest last_seen first), then recent resolutions."""
        ...

    async def record_delivery(
        self,
        alert_id: UUID,
        *,
        provider: str,
        status: str,
        detail: str,
        attempted_at: datetime,
    ) -> None:
        """Persist one delivery attempt outcome onto the open alert row."""
        ...


class InMemoryAlertStore:
    """Process-memory alert rows for tests and non-durable deployments."""

    def __init__(self) -> None:
        """Start with an empty append-only history."""
        self.history: dict[tuple[str, str], list[OperatorAlert]] = {}

    def _open(self, identity: tuple[str, str]) -> OperatorAlert | None:
        """Return the unresolved row for one identity, if any."""
        for alert in reversed(self.history.get(identity, ())):
            if alert.is_open:
                return alert
        return None

    async def record(self, finding: SupervisionFinding, *, now: datetime) -> AlertChange:
        """Insert or refresh the open row for one finding (dedupe by identity)."""
        identity = (finding.code.value, finding.subject)
        existing = self._open(identity)
        if existing is None:
            created = new_alert(finding, now=now)
            self.history.setdefault(identity, []).append(created)
            return AlertChange(alert=created, created=True)
        refreshed = replace(
            existing,
            severity=finding.severity,
            detail=clip_alert_text(finding.detail),
            last_seen_at=now,
            occurrences=existing.occurrences + 1,
            deployment_id=finding.deployment_id,
            product_id=finding.product_id,
        )
        self.history[identity][-1] = refreshed
        return AlertChange(alert=refreshed, created=False)

    async def resolve_absent(
        self, present: Sequence[tuple[AlertCode, str]], *, now: datetime, detail: str
    ) -> int:
        """Resolve open rows whose identity disappeared from this cycle's findings."""
        wanted = {(code.value, subject) for code, subject in present}
        resolved = 0
        for identity in self.history:
            if identity in wanted:
                continue
            open_row = self._open(identity)
            if open_row is None:
                continue
            self.history[identity][-1] = replace(
                open_row, resolved_at=now, resolution_detail=detail
            )
            resolved += 1
        return resolved

    async def list_alerts(self, *, limit: int) -> tuple[OperatorAlert, ...]:
        """Return open rows first (newest last_seen first), then recent resolutions."""
        rows = [row for rows in self.history.values() for row in rows]
        return tuple(_ordered_for_report(rows)[:limit])

    async def record_delivery(
        self,
        alert_id: UUID,
        *,
        provider: str,
        status: str,
        detail: str,
        attempted_at: datetime,
    ) -> None:
        """Persist one delivery attempt onto the matching open row."""
        for rows in self.history.values():
            for index, row in enumerate(rows):
                if row.id != alert_id:
                    continue
                rows[index] = replace(
                    row,
                    delivery_provider=provider,
                    delivery_status=status,
                    delivery_detail=detail[:500],
                    delivery_attempts=row.delivery_attempts + 1,
                    last_seen_at=max(row.last_seen_at, attempted_at),
                )
                return


def _ordered_for_report(rows: Sequence[OperatorAlert]) -> tuple[OperatorAlert, ...]:
    """Open rows first by newest last_seen, then resolutions by newest resolution."""
    open_rows = sorted(
        (row for row in rows if row.is_open), key=lambda row: row.last_seen_at, reverse=True
    )
    resolved_rows = sorted(
        (row for row in rows if not row.is_open),
        key=lambda row: row.resolved_at or row.last_seen_at,
        reverse=True,
    )
    return (*open_rows, *resolved_rows)


class DisabledAlertStore:
    """Fail closed when durable alert storage is not configured."""

    async def record(self, finding: SupervisionFinding, *, now: datetime) -> AlertChange:
        """Refuse alert recording without durable storage."""
        del finding, now
        raise AlertStoreError("Alert storage is unavailable.")

    async def resolve_absent(
        self, present: Sequence[tuple[AlertCode, str]], *, now: datetime, detail: str
    ) -> int:
        """Refuse alert resolution without durable storage."""
        del present, now, detail
        raise AlertStoreError("Alert storage is unavailable.")

    async def list_alerts(self, *, limit: int) -> tuple[OperatorAlert, ...]:
        """Report no rows when storage is unconfigured."""
        del limit
        return ()

    async def record_delivery(
        self,
        alert_id: UUID,
        *,
        provider: str,
        status: str,
        detail: str,
        attempted_at: datetime,
    ) -> None:
        """Refuse delivery bookkeeping without durable storage."""
        del alert_id, provider, status, detail, attempted_at
        raise AlertStoreError("Alert storage is unavailable.")
