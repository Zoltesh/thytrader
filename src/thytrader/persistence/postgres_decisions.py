"""PostgreSQL repository for the bounded per-bar decision journal (ADR 0087)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy import delete, func, literal, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.execution.decision_store import (
    DecisionStoreError,
    decode_decision_cursor,
    encode_decision_cursor_key,
)
from thytrader.execution.decisions import BarDecision, DecisionPage
from thytrader.persistence.schema import bar_decisions

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
    from sqlalchemy.sql import Executable
    from sqlalchemy.sql.elements import ColumnElement

    from thytrader.execution.decisions import DecisionOutcome

_logger = logging.getLogger(__name__)
_KEY = (bar_decisions.c.deployment_id, bar_decisions.c.product_id, bar_decisions.c.bar_starts_at)
_MUTABLE = (
    "strategy_id",
    "mode",
    "timeframe",
    "evaluated_at",
    "outcome",
    "action",
    "reason_code",
    "intent_id",
    "summary",
    "payload_json",
)
_MAX_DEPLOYMENTS_PER_PRUNE = 50


class PostgresDecisionJournalStore:
    """Upsert, page, and prune decision rows using canonical record JSON."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the repository to a managed async engine."""
        self._engine = engine

    async def upsert(self, decision: BarDecision) -> None:
        """Insert or replace the row for one bar of one product."""
        statement = insert(bar_decisions).values(_row_values(decision))
        statement = statement.on_conflict_do_update(
            index_elements=[column.name for column in _KEY],
            set_={name: statement.excluded[name] for name in _MUTABLE},
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise DecisionStoreError("Decision journal storage is unavailable.") from error

    async def latest_before(
        self, deployment_id: UUID, product_id: str, bar_starts_at: datetime
    ) -> BarDecision | None:
        """Return the newest decision for one product strictly before one bar."""
        statement = (
            select(bar_decisions.c.payload_json)
            .where(
                bar_decisions.c.deployment_id == deployment_id,
                bar_decisions.c.product_id == product_id,
                bar_decisions.c.bar_starts_at < bar_starts_at,
            )
            .order_by(bar_decisions.c.bar_starts_at.desc())
            .limit(1)
        )
        rows = await self._fetch(statement)
        decisions = _parse(rows)
        return decisions[0] if decisions else None

    async def list_for_deployment(
        self,
        deployment_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        outcomes: Sequence[DecisionOutcome] = (),
        product_id: str | None = None,
    ) -> DecisionPage:
        """Return one newest-first page of one deployment's decisions."""
        conditions: list[ColumnElement[bool]] = [bar_decisions.c.deployment_id == deployment_id]
        if product_id is not None:
            conditions.append(bar_decisions.c.product_id == product_id)
        return await self._page(conditions, limit=limit, cursor=cursor, outcomes=outcomes)

    async def list_for_strategy(
        self,
        strategy_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        outcomes: Sequence[DecisionOutcome] = (),
        deployment_id: UUID | None = None,
    ) -> DecisionPage:
        """Return one newest-first page across a strategy's deployments."""
        conditions: list[ColumnElement[bool]] = [bar_decisions.c.strategy_id == strategy_id]
        if deployment_id is not None:
            conditions.append(bar_decisions.c.deployment_id == deployment_id)
        return await self._page(conditions, limit=limit, cursor=cursor, outcomes=outcomes)

    async def list_recent(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        outcomes: Sequence[DecisionOutcome] = (),
    ) -> DecisionPage:
        """Return one newest-first page across every bot."""
        return await self._page([], limit=limit, cursor=cursor, outcomes=outcomes)

    async def prune(
        self,
        *,
        now: datetime,
        max_rows_per_deployment: int,
        max_age: timedelta,
        batch_limit: int,
    ) -> int:
        """Delete at most ``batch_limit`` rows older than ``max_age`` or beyond newest-N."""
        try:
            async with self._engine.begin() as connection:
                removed = await _prune_by_age(connection, cutoff=now - max_age, limit=batch_limit)
                if removed < batch_limit:
                    removed += await _prune_by_count(
                        connection, keep=max_rows_per_deployment, limit=batch_limit - removed
                    )
        except SQLAlchemyError as error:
            raise DecisionStoreError("Decision journal retention failed.") from error
        return removed

    async def _page(
        self,
        conditions: list[ColumnElement[bool]],
        *,
        limit: int,
        cursor: str | None,
        outcomes: Sequence[DecisionOutcome],
    ) -> DecisionPage:
        """Run one keyset page ordered by bar, deployment, then product, newest first."""
        if limit < 1:
            raise DecisionStoreError("Decision page limit must be positive.")
        if outcomes:
            conditions.append(bar_decisions.c.outcome.in_([item.value for item in outcomes]))
        if cursor is not None:
            bar, deployment, product = decode_decision_cursor(cursor)
            conditions.append(
                tuple_(
                    bar_decisions.c.bar_starts_at,
                    bar_decisions.c.deployment_id,
                    bar_decisions.c.product_id,
                )
                < tuple_(literal(bar), literal(deployment), literal(product))
            )
        statement = (
            select(bar_decisions.c.payload_json, *_KEY)
            .where(*conditions)
            .order_by(
                bar_decisions.c.bar_starts_at.desc(),
                bar_decisions.c.deployment_id.desc(),
                bar_decisions.c.product_id.desc(),
            )
            .limit(limit + 1)
        )
        rows = await self._fetch(statement)
        page_rows = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page_rows:
            last = page_rows[-1]
            next_cursor = encode_decision_cursor_key(
                bar_starts_at=last["bar_starts_at"],
                deployment_id=last["deployment_id"],
                product_id=last["product_id"],
            )
        return DecisionPage(decisions=_parse(page_rows), next_cursor=next_cursor)

    async def _fetch(self, statement: Executable) -> Sequence[RowMapping]:
        """Execute one read and translate storage failures."""
        try:
            async with self._engine.connect() as connection:
                return (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise DecisionStoreError("Decision journal storage is unavailable.") from error


def _row_values(decision: BarDecision) -> dict[str, object]:
    """Project one decision onto its key, filter columns, and canonical JSON."""
    return {
        "deployment_id": decision.deployment_id,
        "product_id": decision.product_id,
        "bar_starts_at": decision.bar_starts_at,
        "strategy_id": decision.strategy_id,
        "mode": decision.mode.value,
        "timeframe": decision.timeframe,
        "evaluated_at": decision.evaluated_at,
        "outcome": decision.outcome.value,
        "action": decision.action.value,
        "reason_code": decision.reason_code,
        "intent_id": decision.intent_id,
        "summary": decision.summary,
        "payload_json": decision.model_dump_json(),
    }


def _parse(rows: Sequence[RowMapping]) -> tuple[BarDecision, ...]:
    """Validate stored JSON; skip (and log) a row that no longer validates.

    Paging decides ``next_cursor`` from the raw rows before parsing, so a skipped
    row never ends paging early.
    """
    decisions: list[BarDecision] = []
    for row in rows:
        try:
            decisions.append(BarDecision.model_validate_json(row["payload_json"]))
        except ValidationError:
            _logger.warning("decision_journal_row_invalid; skipped one stored decision row")
    return tuple(decisions)


async def _prune_by_age(connection: AsyncConnection, *, cutoff: datetime, limit: int) -> int:
    """Delete up to ``limit`` rows whose bar started before ``cutoff``."""
    doomed = select(*_KEY).where(bar_decisions.c.bar_starts_at < cutoff).limit(limit)
    result = await connection.execute(delete(bar_decisions).where(tuple_(*_KEY).in_(doomed)))
    return int(result.rowcount or 0)


async def _prune_by_count(connection: AsyncConnection, *, keep: int, limit: int) -> int:
    """Delete up to ``limit`` rows beyond each deployment's newest ``keep`` bars."""
    heavy = (
        select(bar_decisions.c.deployment_id)
        .group_by(bar_decisions.c.deployment_id)
        .having(func.count() > keep)
        .limit(_MAX_DEPLOYMENTS_PER_PRUNE)
    )
    deployment_ids = (await connection.execute(heavy)).scalars().all()
    removed = 0
    for deployment_id in deployment_ids:
        if removed >= limit:
            break
        boundary = (
            await connection.execute(
                select(bar_decisions.c.bar_starts_at, bar_decisions.c.product_id)
                .where(bar_decisions.c.deployment_id == deployment_id)
                .order_by(bar_decisions.c.bar_starts_at.desc(), bar_decisions.c.product_id.desc())
                .offset(keep)
                .limit(1)
            )
        ).one_or_none()
        if boundary is None:
            continue
        doomed = (
            select(*_KEY)
            .where(
                bar_decisions.c.deployment_id == deployment_id,
                tuple_(bar_decisions.c.bar_starts_at, bar_decisions.c.product_id)
                <= tuple_(literal(boundary[0]), literal(boundary[1])),
            )
            .limit(limit - removed)
        )
        result = await connection.execute(delete(bar_decisions).where(tuple_(*_KEY).in_(doomed)))
        removed += int(result.rowcount or 0)
    return removed
