"""PostgreSQL atomic observations, monotone recovery, and delivery claims (ADR 0115)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.alerts.models import (
    AlertCheck,
    AlertCode,
    AlertScope,
    AlertSeverity,
    OperatorAlert,
    clip_alert_text,
    new_alert,
)
from thytrader.alerts.store import (
    DELIVERY_DISABLED_DETAIL,
    AlertApplication,
    AlertChange,
    AlertStoreError,
    DeliveryClaim,
    observation_keys,
)
from thytrader.persistence.schema import operator_alert_checks, operator_alerts

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
    from sqlalchemy.sql import Executable

    from thytrader.alerts.models import SupervisionFinding


class PostgresAlertStore:
    """Serialize each check via its watermark and commit an observation batch atomically.

    Watermarks survive resolution and restart. A stale/replayed pass cannot
    increment failures, resolve newer failures, or reopen after a newer recovery.
    All check locks are taken in stable order, avoiding cross-batch deadlocks.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the managed async engine."""
        self._engine = engine

    async def record(self, finding: SupervisionFinding, *, now: datetime) -> AlertChange:
        """Record one positive observation or return its persisted open row."""
        application = await self.apply_observations((finding,), (), now=now, detail="")
        if application.changes:
            return application.changes[0]
        statement = select(operator_alerts).where(
            operator_alerts.c.code == finding.code.value,
            operator_alerts.c.subject == finding.subject,
            operator_alerts.c.resolved_at.is_(None),
        )
        rows = await self._fetch_all(statement)
        if not rows:
            raise AlertStoreError("Stale observation has no open alert.")
        return AlertChange(_parse(rows[0]), False)

    async def apply_observations(
        self,
        findings: Sequence[SupervisionFinding],
        evaluated: Sequence[AlertCheck],
        *,
        now: datetime,
        detail: str,
    ) -> AlertApplication:
        """Accept only strictly newer checks and commit findings/recoveries together."""
        positive = {AlertCheck(item.code, item.subject): item for item in findings}
        changes: list[AlertChange] = []
        resolved = 0
        try:
            async with self._engine.begin() as connection:
                for check in observation_keys(findings, evaluated):
                    if not await _accept_check(
                        connection, check, now=now, failed=check in positive
                    ):
                        continue
                    finding = positive.get(check)
                    if finding is not None:
                        changes.append(await _record(connection, finding, now=now))
                    else:
                        result = await connection.execute(
                            update(operator_alerts)
                            .where(
                                operator_alerts.c.code == check.code.value,
                                operator_alerts.c.subject == check.subject,
                                operator_alerts.c.resolved_at.is_(None),
                                operator_alerts.c.last_seen_at < now,
                            )
                            .values(resolved_at=now, resolution_detail=clip_alert_text(detail))
                        )
                        resolved += int(result.rowcount or 0)
        except SQLAlchemyError as error:
            raise AlertStoreError("Alert observation storage is unavailable.") from error
        return AlertApplication(tuple(changes), resolved)

    async def list_open_alerts(self) -> tuple[OperatorAlert, ...]:
        """Return all open alerts; report pagination cannot become recovery evidence."""
        rows = await self._fetch_all(
            select(operator_alerts).where(operator_alerts.c.resolved_at.is_(None))
        )
        return tuple(_parse(row) for row in rows)

    async def list_alerts(self, *, limit: int) -> tuple[OperatorAlert, ...]:
        """Return open rows first, then most recently resolved."""
        statement = (
            select(operator_alerts)
            .order_by(
                operator_alerts.c.resolved_at.is_(None).desc(),
                operator_alerts.c.resolved_at.desc(),
                operator_alerts.c.last_seen_at.desc(),
            )
            .limit(limit)
        )
        return tuple(_parse(row) for row in await self._fetch_all(statement))

    async def claim_delivery(
        self,
        alert_id: UUID,
        *,
        provider: str,
        now: datetime,
        max_attempts: int,
        ttl: timedelta,
    ) -> DeliveryClaim | None:
        """CAS-claim before send; crash consumes a retry slot and lease eventually expires."""
        conditions = (
            operator_alerts.c.id == alert_id,
            operator_alerts.c.resolved_at.is_(None),
            operator_alerts.c.delivery_status.in_(("pending", "skipped", "failed")),
        )
        token = uuid4()
        if provider == "none":
            statement = (
                update(operator_alerts)
                .where(*conditions)
                .values(
                    delivery_provider=provider,
                    delivery_status="skipped",
                    delivery_detail=DELIVERY_DISABLED_DETAIL,
                    delivery_token=None,
                    delivery_expires_at=None,
                )
            )
        else:
            statement = (
                update(operator_alerts)
                .where(
                    *conditions,
                    operator_alerts.c.delivery_attempts < max_attempts,
                    operator_alerts.c.delivery_expires_at.is_(None)
                    | (operator_alerts.c.delivery_expires_at <= now),
                )
                .values(
                    delivery_provider=provider,
                    delivery_status="failed",
                    delivery_detail="delivery attempt awaiting acknowledgement",
                    delivery_attempts=operator_alerts.c.delivery_attempts + 1,
                    delivery_token=token,
                    delivery_expires_at=now + ttl,
                )
                .returning(operator_alerts)
            )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
                row = result.mappings().first() if provider != "none" else None
        except SQLAlchemyError as error:
            raise AlertStoreError("Alert delivery claim is unavailable.") from error
        return DeliveryClaim(_parse(row), token) if row is not None else None

    async def finish_delivery(
        self, alert_id: UUID, token: UUID, *, status: str, detail: str
    ) -> None:
        """Only a current open claim can acknowledge, never a delayed stale callback."""
        statement = (
            update(operator_alerts)
            .where(
                operator_alerts.c.id == alert_id,
                operator_alerts.c.resolved_at.is_(None),
                operator_alerts.c.delivery_token == token,
            )
            .values(
                delivery_status=status,
                delivery_detail=clip_alert_text(detail),
                delivery_token=None,
                delivery_expires_at=None,
            )
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise AlertStoreError("Alert delivery acknowledgement is unavailable.") from error

    async def _fetch_all(self, statement: Executable) -> tuple[RowMapping, ...]:
        """Read mappings or raise a redacted storage failure (never an empty success)."""
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                return tuple(result.mappings().all())
        except SQLAlchemyError as error:
            raise AlertStoreError("Alert storage is unavailable.") from error


async def _accept_check(
    connection: AsyncConnection, check: AlertCheck, *, now: datetime, failed: bool
) -> bool:
    """Use a persistent watermark; positive failure wins an equal-time healthy observation."""
    newer = operator_alert_checks.c.observed_at < now
    if failed:
        newer = newer | (
            (operator_alert_checks.c.observed_at == now) & operator_alert_checks.c.failed.is_(False)
        )
    statement = (
        insert(operator_alert_checks)
        .values(
            code=check.code.value,
            subject=check.subject,
            observed_at=now,
            failed=failed,
        )
        .on_conflict_do_update(
            index_elements=["code", "subject"],
            set_={"observed_at": now, "failed": failed},
            where=newer,
        )
        .returning(operator_alert_checks.c.code)
    )
    result = await connection.execute(statement)
    return result.first() is not None


async def _record(
    connection: AsyncConnection, finding: SupervisionFinding, *, now: datetime
) -> AlertChange:
    """Upsert an accepted positive observation under the already-held check lock."""
    initial = new_alert(finding, now=now)
    statement = insert(operator_alerts).values(_row_values(initial))
    statement = statement.on_conflict_do_update(
        index_elements=["code", "subject"],
        index_where=operator_alerts.c.resolved_at.is_(None),
        set_={
            "severity": finding.severity.value,
            "detail": clip_alert_text(finding.detail),
            "last_seen_at": now,
            "occurrences": operator_alerts.c.occurrences + int(finding.count_occurrence),
            "deployment_id": finding.deployment_id,
            "product_id": finding.product_id,
        },
    ).returning(operator_alerts)
    result = await connection.execute(statement)
    row = result.mappings().one()
    alert = _parse(row)
    return AlertChange(alert, alert.id == initial.id)


def _row_values(alert: OperatorAlert) -> dict[str, object]:
    """Map validated domain fields to SQL's dynamic insert boundary."""
    return {
        "id": alert.id,
        "code": alert.code.value,
        "scope": alert.scope.value,
        "subject": alert.subject,
        "deployment_id": alert.deployment_id,
        "product_id": alert.product_id,
        "severity": alert.severity.value,
        "detail": alert.detail,
        "first_seen_at": alert.first_seen_at,
        "last_seen_at": alert.last_seen_at,
        "occurrences": alert.occurrences,
        "resolved_at": alert.resolved_at,
        "resolution_detail": alert.resolution_detail,
        "delivery_provider": alert.delivery_provider,
        "delivery_status": alert.delivery_status,
        "delivery_attempts": alert.delivery_attempts,
        "delivery_detail": alert.delivery_detail,
    }


def _parse(row: RowMapping) -> OperatorAlert:
    """Rebuild a domain alert from the database adapter's row boundary."""
    return OperatorAlert(
        id=row["id"],
        code=AlertCode(row["code"]),
        scope=AlertScope(row["scope"]),
        subject=row["subject"],
        severity=AlertSeverity(row["severity"]),
        detail=row["detail"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        occurrences=row["occurrences"],
        resolved_at=row["resolved_at"],
        resolution_detail=row["resolution_detail"],
        deployment_id=row["deployment_id"],
        product_id=row["product_id"],
        delivery_provider=row["delivery_provider"],
        delivery_status=row["delivery_status"],
        delivery_attempts=row["delivery_attempts"],
        delivery_detail=row["delivery_detail"],
        delivery_token=row["delivery_token"],
        delivery_expires_at=row["delivery_expires_at"],
    )
