"""Behavioral tests for the separately supervised market-data ingestion worker."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import Candle, CandleInterval, CandleRangeReport
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.worker_state import (
    InMemoryMarketDataWorkerStateStore,
    MarketDataWorkerAttempt,
    MarketDataWorkerFailure,
    MarketDataWorkerStatus,
)
from thytrader.market_data_worker.service import ingest_once, run_market_data_worker

if TYPE_CHECKING:
    from pathlib import Path


class _StubRangeService:
    """Deterministic provider-neutral range service with controllable outcomes."""

    def __init__(self, outcomes: list[CandleRangeReport | Exception]) -> None:
        self._outcomes = outcomes
        self.requests: list[tuple[str, datetime, datetime, datetime]] = []

    async def get_hourly_range(
        self,
        product_id: str,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Return the next configured report or raise its configured failure."""
        self.requests.append((product_id, starts_at, ends_at, now))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _report(*, starts_at: datetime, candle_count: int) -> CandleRangeReport:
    """Create one complete aligned hourly report for worker behavior tests."""
    candles = tuple(
        Candle(
            starts_at=starts_at + timedelta(hours=index),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("12.5"),
        )
        for index in range(candle_count)
    )
    ends_at = starts_at + timedelta(hours=candle_count)
    return analyze_range(
        candles,
        CandleInterval.ONE_HOUR,
        starts_at,
        ends_at,
        now=ends_at,
    )


def test_ingest_once_publishes_verified_complete_range_and_success_state(tmp_path: Path) -> None:
    """One successful attempt publishes through DatasetStore and records exact coverage facts."""

    async def exercise() -> None:
        ends_at = datetime(2026, 7, 29, 2, tzinfo=UTC)
        report = _report(starts_at=ends_at - timedelta(hours=3), candle_count=3)
        service = _StubRangeService([report])
        state_store = InMemoryMarketDataWorkerStateStore()

        await ingest_once(
            service=service,
            dataset_store=DatasetStore(tmp_path),
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USD",
            lookback_hours=3,
            now=ends_at + timedelta(minutes=17),
        )

        state = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert state is not None
        assert state.status is MarketDataWorkerStatus.SUCCEEDED
        assert state.requested_starts_at == report.starts_at
        assert state.requested_ends_at == report.ends_at
        assert state.covered_starts_at == report.starts_at
        assert state.covered_ends_at == report.ends_at
        assert state.expected_candle_count == 3
        assert state.received_candle_count == 3
        assert state.gap_count == 0
        assert state.missing_intervals == 0
        assert state.complete is True
        assert state.content_fingerprint is not None
        assert state.last_success_at == ends_at + timedelta(minutes=17)
        assert state.failure_code is None
        assert state.consecutive_failures == 0
        assert len(tuple((tmp_path / "manifests").glob("*.json"))) == 1

    asyncio.run(exercise())


def test_ingest_once_records_redacted_failure_without_publishing(tmp_path: Path) -> None:
    """Provider failures remain durable and publish no misleading dataset or coverage facts."""

    async def exercise() -> None:
        now = datetime(2026, 7, 29, 2, 17, tzinfo=UTC)
        service = _StubRangeService([RuntimeError("upstream body with sensitive details")])
        state_store = InMemoryMarketDataWorkerStateStore()

        await ingest_once(
            service=service,
            dataset_store=DatasetStore(tmp_path),
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USD",
            lookback_hours=3,
            now=now,
        )

        state = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert state is not None
        assert state.status is MarketDataWorkerStatus.FAILED
        assert state.failure_code == "provider_unavailable"
        assert state.failure_message == "Historical market-data retrieval failed."
        assert "sensitive" not in state.failure_message
        assert state.covered_starts_at is None
        assert state.content_fingerprint is None
        assert state.consecutive_failures == 1
        assert not (tmp_path / "manifests").exists()

    asyncio.run(exercise())


def test_ingest_once_rejects_incomplete_report_without_publishing(tmp_path: Path) -> None:
    """A provider report that is not complete must never reach DatasetStore publication."""

    async def exercise() -> None:
        ends_at = datetime(2026, 7, 29, 2, tzinfo=UTC)
        complete_report = _report(starts_at=ends_at - timedelta(hours=3), candle_count=3)
        service = _StubRangeService([replace(complete_report, complete=False)])
        state_store = InMemoryMarketDataWorkerStateStore()

        await ingest_once(
            service=service,
            dataset_store=DatasetStore(tmp_path),
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USD",
            lookback_hours=3,
            now=ends_at,
        )

        state = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert state is not None
        assert state.status is MarketDataWorkerStatus.FAILED
        assert state.failure_code == "incomplete_range"
        assert not (tmp_path / "manifests").exists()

    asyncio.run(exercise())


def test_ingest_once_recovery_clears_failure_and_preserves_last_success(tmp_path: Path) -> None:
    """A later successful retry replaces failure diagnostics with verified coverage evidence."""

    async def exercise() -> None:
        ends_at = datetime(2026, 7, 29, 2, tzinfo=UTC)
        report = _report(starts_at=ends_at - timedelta(hours=3), candle_count=3)
        service = _StubRangeService([RuntimeError("temporary"), report])
        state_store = InMemoryMarketDataWorkerStateStore()
        dataset_store = DatasetStore(tmp_path)

        await ingest_once(
            service=service,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USD",
            lookback_hours=3,
            now=ends_at + timedelta(minutes=5),
        )
        await ingest_once(
            service=service,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USD",
            lookback_hours=3,
            now=ends_at + timedelta(minutes=10),
        )

        state = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert state is not None
        assert state.status is MarketDataWorkerStatus.SUCCEEDED
        assert state.failure_code is None
        assert state.consecutive_failures == 0
        assert state.last_success_at == ends_at + timedelta(minutes=10)

    asyncio.run(exercise())


def test_new_attempt_preserves_previous_failure_until_verified_success() -> None:
    """A retry must not erase durable failure evidence before it has succeeded."""

    async def exercise() -> None:
        store = InMemoryMarketDataWorkerStateStore()
        first = MarketDataWorkerAttempt(
            provider="coinbase",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            attempted_at=datetime(2026, 7, 29, 2, tzinfo=UTC),
            requested_starts_at=datetime(2026, 7, 28, 23, tzinfo=UTC),
            requested_ends_at=datetime(2026, 7, 29, 2, tzinfo=UTC),
        )
        await store.record_attempt(first)
        await store.record_failure(
            MarketDataWorkerFailure(first, "provider_unavailable", "Retrieval failed.")
        )
        retry = replace(first, attempted_at=first.attempted_at + timedelta(minutes=5))
        await store.record_attempt(retry)

        state = await store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert state is not None
        assert state.status is MarketDataWorkerStatus.RUNNING
        assert state.failure_code == "provider_unavailable"
        assert state.consecutive_failures == 1

    asyncio.run(exercise())


def test_market_data_worker_readiness_tracks_only_its_own_run_loop(tmp_path: Path) -> None:
    """The dedicated worker reports ready while active and clears readiness on shutdown."""

    async def exercise() -> None:
        now = datetime(2026, 7, 29, 2, tzinfo=UTC)
        report = _report(starts_at=now - timedelta(hours=3), candle_count=3)
        stop = asyncio.Event()
        readiness: list[bool] = []
        task = asyncio.create_task(
            run_market_data_worker(
                stop,
                service=_StubRangeService([report]),
                dataset_store=DatasetStore(tmp_path),
                state_store=InMemoryMarketDataWorkerStateStore(),
                provider="coinbase",
                product_id="BTC-USD",
                lookback_hours=3,
                interval_seconds=60,
                now_factory=lambda: now,
                on_readiness_changed=readiness.append,
            )
        )
        await asyncio.sleep(0.05)
        assert readiness == [True]
        stop.set()
        await task
        assert readiness == [True, False]

    asyncio.run(exercise())
