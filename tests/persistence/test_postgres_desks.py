"""PostgreSQL keeps desks, their funded books and every wake (ADR 0147)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os

from pydantic import SecretStr
import pytest

from tests.persistence.test_postgres_fill_transaction import _pending_entry_book
from thytrader.agent_desk.models import (
    Desk,
    DeskAction,
    DeskBook,
    DeskStatus,
    DeskWake,
    WakeOutcome,
)
from thytrader.agent_desk.store import DeskConflictError
from thytrader.ids import uuid7
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_desks import PostgresDeskStore
from thytrader.persistence.postgres_execution import PostgresExecutionStore

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


def _wake(desk_id: str, started_at: datetime, outcome: WakeOutcome) -> DeskWake:
    """One synthetic journaled wake."""
    return DeskWake(
        wake_id=str(uuid7(started_at)),
        desk_id=desk_id,
        started_at=started_at,
        finished_at=started_at,
        harness="test",
        briefing_fingerprint="sha256:" + "0" * 64,
        raw_response="{}",
        action=DeskAction.NO_TRADE,
        outcome=outcome,
        equity_quote=Decimal(10000),
    )


@pytest.mark.anyio
async def test_postgres_desk_round_trip() -> None:
    """Desks keep optimistic revisions; books settle in place; wakes list newest first."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    engine = create_engine(SecretStr(_TEST_DATABASE_URL))
    try:
        store = PostgresDeskStore(engine)
        now = datetime.now(UTC)
        desk = await store.create_desk(
            Desk(
                desk_id=str(uuid7(now)),
                name="pg desk",
                quote_currency="USD",
                starting_cash=Decimal(10000),
                cash=Decimal(10000),
                products=("BTC-USD",),
                created_at=now,
                updated_at=now,
            )
        )
        assert await store.get_desk(desk.desk_id) == desk
        assert desk.desk_id in {row.desk_id for row in await store.list_desks()}
        saved = await store.save_desk(
            desk.model_copy(update={"cash": Decimal(9000)}), expected_revision=desk.revision
        )
        assert saved.revision == desk.revision + 1
        with pytest.raises(DeskConflictError):
            await store.save_desk(
                saved.model_copy(update={"status": DeskStatus.PAUSED}),
                expected_revision=desk.revision,
            )
        deployment = await PostgresExecutionStore(engine).create_deployment(_pending_entry_book())
        book = DeskBook(
            desk_id=desk.desk_id,
            deployment_id=str(deployment.id),
            product_id="BTC-USD",
            side="long",
            allocated_quote=Decimal(1000),
            opened_at=now,
            wake_id="w",
        )
        funded = await store.save_desk(saved, expected_revision=saved.revision, books=(book,))
        assert await store.list_books(desk.desk_id) == (book,)
        settled = book.model_copy(update={"settled_at": now, "settled_cash": Decimal("1012.5")})
        with pytest.raises(DeskConflictError):
            await store.save_desk(funded, expected_revision=saved.revision, books=(settled,))
        assert await store.list_books(desk.desk_id) == (book,)
        await store.save_desk(funded, expected_revision=funded.revision, books=(settled,))
        assert await store.list_books(desk.desk_id) == (settled,)
        first = _wake(desk.desk_id, now, WakeOutcome.APPLIED)
        second = _wake(desk.desk_id, now + timedelta(minutes=30), WakeOutcome.INVALID)
        await store.add_wake(first)
        await store.add_wake(second)
        assert await store.list_wakes(desk.desk_id, limit=10) == (second, first)
        assert await store.list_wakes(desk.desk_id, limit=1) == (second,)
        assert await store.last_equity_before(desk.desk_id, second.started_at) == Decimal(10000)
        assert await store.last_equity_before(desk.desk_id, now) is None
        third = _wake(desk.desk_id, now + timedelta(hours=1), WakeOutcome.APPLIED)
        latest = await store.get_desk(desk.desk_id)
        assert latest is not None
        await store.save_desk(latest, expected_revision=latest.revision, wakes=(third,))
        assert (await store.list_wakes(desk.desk_id, limit=1))[0] == third
    finally:
        await dispose(engine)
