"""PostgreSQL repository for durable operator safety alerts (ADR 0115)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.alerts.models import (
    AlertCode,
    AlertScope,
    AlertSeverity,
    OperatorAlert,
    clip_alert_text,
    new_alert,
)
from thytrader.alerts.store import AlertChange, AlertStoreError
from thytrader.persistence.schema import operator_alerts

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.sql import Executable
    from sqlalchemy.sql.elements import ColumnElement

    from thytrader.alerts.models import SupervisionFinding


class PostgresAlertStore:
    """Upsert, resolve, page, and annotate alert rows on PostgreSQL.

    Dedupe uses the partial unique index ``ux_operator_alerts_open`` on
    ``(code, subject) WHERE resolved_at IS NULL``: concurrent recorders collapse
    onto one open row, and a resolution frees the identity for a fresh row.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the repository to a managed async engine."""
        self._engine = engine

    async def record(self, finding: SupervisionFinding, *, now: datetime) -> AlertChange:
        """Insert or refresh the open alert row for one finding."""
        statement = (
            insert(operator_alerts)
            .values(_row_values(new_alert(finding, now=now)))
            .on_conflict_do_update(
                index_elements=["code", "subject"],
                index_where=operator_alerts.c.resolved_at.is_(None),
                set_={
                    "severity": finding.severity.value,
                    "detail": clip_alert_text(finding.detail),
                    "last_seen_at": now,
                    "occurrences": operator_alerts.c.occurrences + 1,
                    "deployment_id": finding.deployment_id,
                    "product_id": finding.product_id,
                },
            )
            .returning(operator_alerts)
        )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
                row = result.mappings().first()
        except SQLAlchemyError as error:
            raise AlertStoreError("Alert storage is unavailable.") from error
        if row is None:
            raise AlertStoreError("Alert recording returned no row.")
        alert = _parse(row)
        return AlertChange(alert=alert, created=alert.occurrences == 1)

    async def resolve_absent(
        self, present: Sequence[tuple[AlertCode, str]], *, now: datetime, detail: str
    ) -> int:
        """Resolve open rows whose identity is absent from this cycle's findings.

        A row opened by a concurrent recorder after this pass's snapshot simply
        resolves on the next pass; findings are recomputed every cycle.
        """
        identities = [(code.value, subject) for code, subject in present]
        conditions: list[ColumnElement[bool]] = [operator_alerts.c.resolved_at.is_(None)]
        if identities:
            conditions.append(
                ~tuple_(operator_alerts.c.code, operator_alerts.c.subject).in_(identities)
            )
        statement = (
            update(operator_alerts)
            .where(*conditions)
            .values(resolved_at=now, resolution_detail=detail)
        )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
                return int(result.rowcount or 0)
        except SQLAlchemyError as error:
            raise AlertStoreError("Alert resolution is unavailable.") from error

    async def list_alerts(self, *, limit: int) -> tuple[OperatorAlert, ...]:
        """Return open rows first (newest last_seen first), then recent resolutions."""
        statement = (
            select(operator_alerts)
            .order_by(
                operator_alerts.c.resolved_at.is_(None).desc(),
                operator_alerts.c.last_seen_at.desc(),
            )
            .limit(limit)
        )
        return tuple(_parse(row) for row in await self._fetch_all(statement))

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
        statement = (
            update(operator_alerts)
            .where(operator_alerts.c.id == alert_id, operator_alerts.c.resolved_at.is_(None))
            .values(
                delivery_provider=provider,
                delivery_status=status,
                delivery_detail=detail[:500],
                delivery_attempts=operator_alerts.c.delivery_attempts + 1,
                last_seen_at=func.greatest(operator_alerts.c.last_seen_at, attempted_at),
            )
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise AlertStoreError("Alert delivery bookkeeping is unavailable.") from error

    async def _fetch_all(self, statement: Executable) -> tuple[RowMapping, ...]:
        """Execute one statement and return every row, mapped."""
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                return tuple(result.mappings().all())
        except SQLAlchemyError as error:
            raise AlertStoreError("Alert storage is unavailable.") from error


def _row_values(alert: OperatorAlert) -> dict[str, object]:
    """Map one alert onto insertable column values."""
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
    """Rebuild one alert from a stored row."""
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
    )
