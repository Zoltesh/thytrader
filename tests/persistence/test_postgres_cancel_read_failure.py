"""Accepted cancels remain GET-only after SQL reload despite confirmation outages."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from tests.execution.test_live_futures_entry import clock
from tests.execution_worker.test_futures_cancel_read_failure import (
    ConfirmationFailureVenue,
    exercise_cancel_outage,
)
from tests.persistence.test_postgres_futures_books import _run
from tests.persistence.test_postgres_live_futures import _open
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.trading.models import LifecycleCommand

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None, reason="Disposable PostgreSQL required."
)


@pytest.mark.parametrize("fault", ["once", "persistent", "http"])
@pytest.mark.parametrize("command", [LifecycleCommand.MANAGED_SHUTDOWN, LifecycleCommand.FLATTEN])
def test_postgres_accepted_cancel_survives_read_fault_and_store_reload(
    monkeypatch: pytest.MonkeyPatch, fault: str, command: LifecycleCommand
) -> None:
    """Fresh stores and adapters recover the pending marker without resending the mutation."""

    async def body(engine: AsyncEngine) -> None:
        """Exercise real worker, SQL rows and canonical financial completion."""
        venue = ConfirmationFailureVenue(fault)
        clock(monkeypatch, venue)
        store, snapshot = await _open(engine, venue)
        await exercise_cancel_outage(
            store, venue, snapshot, command, PostgresExecutionStore(engine)
        )

    _run(body)
