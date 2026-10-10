"""Time every SQL statement an engine executes into the bound ledger (ADR 0131)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from sqlalchemy import event

from thytrader.observability.database_calls import record_database_call

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, ExceptionContext
    from sqlalchemy.ext.asyncio import AsyncEngine

_STARTS = "thytrader_statement_starts"


def instrument_engine(engine: AsyncEngine) -> None:
    """Record each statement's latency; only counts and durations, never SQL or rows."""
    sync_engine = engine.sync_engine

    def before(conn: Connection, *args: object) -> None:
        """Stack the statement start on the connection."""
        del args
        starts = conn.info.setdefault(_STARTS, [])
        starts.append(time.perf_counter())

    def after(conn: Connection, *args: object) -> None:
        """Record the finished statement."""
        del args
        starts = conn.info.get(_STARTS)
        if starts:
            record_database_call(time.perf_counter() - starts.pop())

    def failed(context: ExceptionContext) -> None:
        """Record a failed statement too, so its time is not lost."""
        conn = context.connection
        starts = None if conn is None else conn.info.get(_STARTS)
        if starts:
            record_database_call(time.perf_counter() - starts.pop())

    event.listen(sync_engine, "before_cursor_execute", before)
    event.listen(sync_engine, "after_cursor_execute", after)
    event.listen(sync_engine, "handle_error", failed)
