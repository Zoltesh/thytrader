"""Live PostgreSQL tests for futures observations and settled funding history (ADR 0126)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy import select

from thytrader.exchanges.coinbase_futures_catalog import parse_futures_row
from thytrader.market_data.futures_observations import (
    FundingSample,
    FuturesInstrumentObservation,
    instrument_observation,
)
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_futures import PostgresFuturesObservationStore
from thytrader.persistence.schema import futures_instrument_observations

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncEngine

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
_FIXTURE = Path(__file__).parents[1] / "exchanges" / "fixtures" / "coinbase_futures_listing.json"
_T0 = datetime(2026, 10, 10, 0, tzinfo=UTC)
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
            await body(engine)
        finally:
            await dispose(engine)

    asyncio.run(exercise())


def _product_id() -> str:
    """Return a unique futures-shaped id so tests sharing the database never collide."""
    return f"Z{uuid4().hex[:5].upper()}-20DEC30-CDE"


def _observation(product_id: str) -> FuturesInstrumentObservation:
    """Return the BIP fixture row's recorded facts under a unique id."""
    payload: dict[str, Any] = json.loads(_FIXTURE.read_text())
    row = next(item for item in payload["products"] if item["product_id"] == "BIP-20DEC30-CDE")
    product = parse_futures_row(row)
    assert product is not None
    return replace(instrument_observation(product), product_id=product_id)


def _sample(product_id: str, hour: int, rate: str) -> FundingSample:
    """One hourly sample at ``_T0 + hour``."""
    return FundingSample(
        product_id=product_id,
        funding_time=_T0 + timedelta(hours=hour),
        rate=Decimal(rate),
        interval_seconds=3600,
    )


def _store(engine: AsyncEngine) -> PostgresFuturesObservationStore:
    """A store with its own provider label, so its poll-state row is private."""
    return PostgresFuturesObservationStore(engine, provider=f"t-{uuid4().hex[:12]}")


async def _poll(
    store: PostgresFuturesObservationStore,
    at_minutes: int,
    samples: tuple[FundingSample, ...],
    observations: tuple[FuturesInstrumentObservation, ...] = (),
) -> Any:
    """Record one poll at ``_T0 + at_minutes``."""
    return await store.record_poll(
        observed_at=_T0 + timedelta(minutes=at_minutes),
        observations=observations,
        samples=samples,
        listing_fingerprint="sha256:" + "0" * 64,
        perpetual_count=len(samples),
    )


def test_current_hour_revises_then_settles_when_the_listing_moves_on() -> None:
    """The settled rate is the last value seen while the hour was current."""

    async def body(engine: AsyncEngine) -> None:
        """Poll the same hour three times, then the next hour."""
        store = _store(engine)
        pid = _product_id()
        first = await _poll(store, 5, (_sample(pid, 0, "0.000010"),))
        assert first.inserted == 1
        await _poll(store, 10, (_sample(pid, 0, "0.0000100"),))
        await _poll(store, 15, (_sample(pid, 0, "0.000012"),))
        moved = await _poll(store, 65, (_sample(pid, 1, "0.000020"),))
        assert (moved.inserted, moved.settled, moved.conflicts) == (1, 1, ())
        rows = await store.funding_rates(
            product_id=pid, starts_at=_T0, ends_at=_T0 + timedelta(hours=2)
        )
        settled, current = rows
        assert settled.rate == Decimal("0.000012")
        assert (settled.observation_count, settled.revision_count) == (3, 1)
        assert settled.settled is True
        assert settled.settled_at == _T0 + timedelta(minutes=65)
        assert current.settled is False
        assert current.rate == Decimal("0.000020")
        assert (await store.funding_history_starts())[pid] == _T0

    _run(body)


def test_settled_rate_is_immutable_and_a_disagreement_is_a_conflict() -> None:
    """A later different value for a settled hour is counted, never written over."""

    async def body(engine: AsyncEngine) -> None:
        """Settle hour 0, then replay it with the same and a different rate."""
        store = _store(engine)
        pid = _product_id()
        await _poll(store, 5, (_sample(pid, 0, "0.000010"),))
        await _poll(store, 65, (_sample(pid, 1, "0.000011"),))
        same = await _poll(store, 70, (_sample(pid, 0, "0.000010"),))
        assert same.conflicts == ()
        conflict = await _poll(store, 75, (_sample(pid, 0, "-0.000040"),))
        assert [sample.rate for sample in conflict.conflicts] == [Decimal("-0.000040")]
        rows = await store.funding_rates(
            product_id=pid, starts_at=_T0, ends_at=_T0 + timedelta(hours=1)
        )
        (row,) = rows
        assert row.rate == Decimal("0.000010")
        assert row.conflict_count == 1
        assert row.last_conflict_rate == Decimal("-0.000040")
        assert row.observation_count == 1

    _run(body)


def test_instrument_rows_extend_until_the_payload_changes() -> None:
    """Unchanged facts extend ``last_seen_at``; a margin change starts a new row."""

    async def body(engine: AsyncEngine) -> None:
        """Poll the same contract twice unchanged, then with a new margin rate."""
        store = _store(engine)
        pid = _product_id()
        observation = _observation(pid)
        await _poll(store, 5, (), (observation,))
        await _poll(store, 10, (), (observation,))
        changed = replace(observation, overnight_short_margin_rate="0.30")
        await _poll(store, 15, (), (changed,))
        table = futures_instrument_observations
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    select(table).where(table.c.product_id == pid).order_by(table.c.first_seen_at)
                )
            ).all()
        assert len(rows) == 2
        assert rows[0].last_seen_at == _T0 + timedelta(minutes=10)
        assert rows[0].payload_fingerprint == observation.payload_fingerprint
        assert rows[1].overnight_short_margin_rate == "0.30"
        assert rows[1].underlying == "BTC"
        assert (await store.trades_around_the_clock())[pid] is True

    _run(body)


def test_poll_failures_count_without_touching_the_last_success() -> None:
    """A failed poll increments the streak; the next success resets it."""

    async def body(engine: AsyncEngine) -> None:
        """Succeed, fail twice, succeed."""
        store = _store(engine)
        assert await store.poll_state() is None
        await _poll(store, 5, ())
        for minutes in (10, 15):
            await store.record_poll_failure(
                attempted_at=_T0 + timedelta(minutes=minutes),
                failure_code="FUTURES_LISTING_UNAVAILABLE",
            )
        state = await store.poll_state()
        assert state is not None
        assert state.consecutive_failures == 2
        assert state.last_success_at == _T0 + timedelta(minutes=5)
        assert state.failure_code == "FUTURES_LISTING_UNAVAILABLE"
        await _poll(store, 20, ())
        recovered = await store.poll_state()
        assert recovered is not None
        assert (recovered.consecutive_failures, recovered.failure_code) == (0, None)

    _run(body)
