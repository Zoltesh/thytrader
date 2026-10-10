"""Live PostgreSQL tests for execution cycle timing records (ADR 0131)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import os
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy import delete, select

from tests.operator_diagnostics.test_execution_cycle_report import _report
from thytrader.observability.database_calls import DatabaseCallLedger, database_call_scope
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution_cycles import RETENTION, PostgresExecutionCycleStore
from thytrader.persistence.query_timing import instrument_engine
from thytrader.persistence.schema import execution_cycles

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncEngine

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


def _run(body: Callable[[AsyncEngine], Awaitable[None]]) -> None:
    """Run one async body against the throwaway test database."""

    async def exercise() -> None:
        """Own the engine lifecycle."""
        if _TEST_DATABASE_URL is None:
            raise AssertionError("PostgreSQL integration URL was not configured.")
        engine = create_engine(SecretStr(_TEST_DATABASE_URL))
        try:
            # Only these tests write cycle telemetry; start each from an empty table.
            async with engine.begin() as connection:
                await connection.execute(delete(execution_cycles))
            await body(engine)
        finally:
            await dispose(engine)

    asyncio.run(exercise())


def _future_instant() -> datetime:
    """A strictly newer instant than anything earlier tests wrote."""
    return datetime.now(UTC).replace(microsecond=0) + timedelta(days=3650)


def test_started_cycle_completes_with_its_report_newest_first() -> None:
    """A started cycle reads back unfinished, then with the exact report once completed."""

    async def body(engine: AsyncEngine) -> None:
        store = PostgresExecutionCycleStore(engine)
        first = _report(started_at=_future_instant(), duration=12.5)
        await store.start_cycle(first.cycle_id, first.started_at, first.interval_seconds)
        await store.complete_cycle(first)
        running_id = uuid4()
        running_at = first.started_at + timedelta(seconds=40)
        await store.start_cycle(running_id, running_at, 30)
        records = await store.recent_cycles(2)
        assert [record.cycle_id for record in records] == [running_id, first.cycle_id]
        assert records[0].report is None
        assert records[0].completed_at is None
        assert records[0].started_at == running_at
        assert records[1].report == first
        assert records[1].completed_at == first.completed_at

    _run(body)


def test_completion_without_a_stored_start_inserts_the_cycle() -> None:
    """A lost start write does not lose the report."""

    async def body(engine: AsyncEngine) -> None:
        store = PostgresExecutionCycleStore(engine)
        report = _report(started_at=_future_instant() + timedelta(hours=1), duration=3.0)
        await store.complete_cycle(report)
        records = await store.recent_cycles(1)
        assert records[0].report == report

    _run(body)


def test_starting_a_cycle_prunes_rows_older_than_one_day() -> None:
    """Retention keeps one day of cycles relative to the newest start."""

    async def body(engine: AsyncEngine) -> None:
        store = PostgresExecutionCycleStore(engine)
        newest = _future_instant() + timedelta(days=2)
        old_id = uuid4()
        await store.start_cycle(old_id, newest - RETENTION - timedelta(minutes=1), 30)
        await store.start_cycle(uuid4(), newest, 30)
        async with engine.connect() as connection:
            remaining = (
                await connection.execute(
                    select(execution_cycles.c.cycle_id).where(execution_cycles.c.cycle_id == old_id)
                )
            ).all()
        assert remaining == []

    _run(body)


def test_instrumented_engine_records_statements_into_the_bound_ledger() -> None:
    """Every statement inside a bound scope is counted; none outside it."""

    async def body(engine: AsyncEngine) -> None:
        instrument_engine(engine)
        store = PostgresExecutionCycleStore(engine)
        ledger = DatabaseCallLedger()
        await store.recent_cycles(1)
        with database_call_scope(ledger):
            await store.recent_cycles(1)
            await store.start_cycle(uuid4(), _future_instant() + timedelta(days=3), 30)
        totals = ledger.totals()
        assert totals.requests == 3
        assert totals.seconds > 0

    _run(body)
