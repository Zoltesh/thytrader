"""Behavioral tests for the separately supervised market-data ingestion worker."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import Candle, CandleInterval, CandleRangeReport
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.worker_state import (
    InMemoryMarketDataWorkerStateStore,
    MarketDataWorkerAttempt,
    MarketDataWorkerError,
    MarketDataWorkerFailure,
    MarketDataWorkerStatus,
    MarketDataWorkerSuccess,
)
from thytrader.market_data_worker.service import _next_retry_at, ingest_once, run_market_data_worker

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


def test_ingest_once_rejects_unrepresentable_initial_range(tmp_path: Path) -> None:
    """A minimum-date initial backfill must fail as a controlled worker error."""

    async def exercise() -> None:
        with pytest.raises(MarketDataWorkerError, match="represent"):
            await ingest_once(
                service=_StubRangeService([]),
                dataset_store=DatasetStore(tmp_path),
                state_store=InMemoryMarketDataWorkerStateStore(),
                provider="coinbase",
                product_id="BTC-USD",
                lookback_hours=3,
                now=datetime.min.replace(tzinfo=UTC),
            )

    asyncio.run(exercise())


def test_next_retry_at_rejects_unrepresentable_schedule() -> None:
    """Maximum-date retry arithmetic must not leak raw datetime overflow."""
    with pytest.raises(MarketDataWorkerError, match="represent"):
        _next_retry_at(datetime.max.replace(tzinfo=UTC), 300, 0, 0.0)


def test_ingest_once_rejects_forged_naive_persisted_coverage(tmp_path: Path) -> None:
    """A forged persisted coverage timestamp must fail before worker comparisons or I/O."""

    async def exercise() -> None:
        ends_at = datetime(2026, 7, 29, 2, tzinfo=UTC)
        report = _report(starts_at=ends_at - timedelta(hours=3), candle_count=3)
        state_store = InMemoryMarketDataWorkerStateStore()
        attempt = MarketDataWorkerAttempt(
            provider="coinbase",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            attempted_at=ends_at,
            requested_starts_at=report.starts_at,
            requested_ends_at=report.ends_at,
        )
        await state_store.record_attempt(attempt)
        await state_store.record_success(
            MarketDataWorkerSuccess(
                attempt=attempt,
                covered_starts_at=report.starts_at,
                covered_ends_at=report.ends_at,
                expected_candle_count=3,
                received_candle_count=3,
                gap_count=0,
                missing_intervals=0,
                content_fingerprint="sha256:" + "a" * 64,
            )
        )
        state = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert state is not None
        object.__setattr__(state, "covered_ends_at", datetime.fromisoformat("2026-07-29T02:00:00"))
        service = _StubRangeService([])

        with pytest.raises(MarketDataWorkerError, match="UTC"):
            await ingest_once(
                service=service,
                dataset_store=DatasetStore(tmp_path),
                state_store=state_store,
                provider="coinbase",
                product_id="BTC-USD",
                lookback_hours=3,
                now=ends_at,
            )

        assert service.requests == []

    asyncio.run(exercise())


def test_ingest_once_rejects_forged_negative_persisted_failure_count(tmp_path: Path) -> None:
    """A malformed persisted retry counter must fail before provider or dataset I/O."""

    async def exercise() -> None:
        now = datetime(2026, 7, 29, 2, tzinfo=UTC)
        state_store = InMemoryMarketDataWorkerStateStore()
        attempt = MarketDataWorkerAttempt(
            provider="coinbase",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            attempted_at=now,
            requested_starts_at=now - timedelta(hours=3),
            requested_ends_at=now,
        )
        await state_store.record_attempt(attempt)
        state = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert state is not None
        object.__setattr__(state, "consecutive_failures", -1)
        service = _StubRangeService([])

        with pytest.raises(MarketDataWorkerError, match="consecutive_failures"):
            await ingest_once(
                service=service,
                dataset_store=DatasetStore(tmp_path),
                state_store=state_store,
                provider="coinbase",
                product_id="BTC-USD",
                lookback_hours=3,
                now=now + timedelta(hours=1),
            )

        assert service.requests == []

    asyncio.run(exercise())


def test_ingest_once_rejects_forged_coverage_count_before_provider_io(tmp_path: Path) -> None:
    """A complete persisted range must retain the exact count implied by its interval."""

    async def exercise() -> None:
        ends_at = datetime(2026, 7, 29, 2, tzinfo=UTC)
        state_store = InMemoryMarketDataWorkerStateStore()
        attempt = MarketDataWorkerAttempt(
            provider="coinbase",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            attempted_at=ends_at,
            requested_starts_at=ends_at - timedelta(hours=3),
            requested_ends_at=ends_at,
        )
        await state_store.record_attempt(attempt)
        await state_store.record_success(
            MarketDataWorkerSuccess(
                attempt=attempt,
                covered_starts_at=attempt.requested_starts_at,
                covered_ends_at=attempt.requested_ends_at,
                expected_candle_count=3,
                received_candle_count=3,
                gap_count=0,
                missing_intervals=0,
                content_fingerprint="sha256:" + "a" * 64,
            )
        )
        state = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert state is not None
        object.__setattr__(state, "expected_candle_count", 1)
        object.__setattr__(state, "received_candle_count", 1)
        service = _StubRangeService([])

        with pytest.raises(MarketDataWorkerError, match="coverage count"):
            await ingest_once(
                service=service,
                dataset_store=DatasetStore(tmp_path),
                state_store=state_store,
                provider="coinbase",
                product_id="BTC-USD",
                lookback_hours=3,
                now=ends_at + timedelta(hours=1),
            )

        assert service.requests == []

    asyncio.run(exercise())


def test_ingest_once_rejects_coverage_without_success_before_provider_io(tmp_path: Path) -> None:
    """Persisted verified coverage must retain the success instant that established it."""

    async def exercise() -> None:
        ends_at = datetime(2026, 7, 29, 2, tzinfo=UTC)
        state_store = InMemoryMarketDataWorkerStateStore()
        attempt = MarketDataWorkerAttempt(
            provider="coinbase",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            attempted_at=ends_at,
            requested_starts_at=ends_at - timedelta(hours=3),
            requested_ends_at=ends_at,
        )
        await state_store.record_attempt(attempt)
        await state_store.record_success(
            MarketDataWorkerSuccess(
                attempt=attempt,
                covered_starts_at=attempt.requested_starts_at,
                covered_ends_at=attempt.requested_ends_at,
                expected_candle_count=3,
                received_candle_count=3,
                gap_count=0,
                missing_intervals=0,
                content_fingerprint="sha256:" + "a" * 64,
            )
        )
        state = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert state is not None
        object.__setattr__(state, "last_success_at", None)
        service = _StubRangeService([])

        with pytest.raises(MarketDataWorkerError, match="success instant"):
            await ingest_once(
                service=service,
                dataset_store=DatasetStore(tmp_path),
                state_store=state_store,
                provider="coinbase",
                product_id="BTC-USD",
                lookback_hours=3,
                now=ends_at + timedelta(hours=1),
            )

        assert service.requests == []

    asyncio.run(exercise())


def test_ingest_once_rejects_failed_state_without_retry_deadline(tmp_path: Path) -> None:
    """A failed persisted state must not bypass durable retry scheduling before provider I/O."""

    async def exercise() -> None:
        attempted_at = datetime(2026, 7, 29, 2, tzinfo=UTC)
        state_store = InMemoryMarketDataWorkerStateStore()
        attempt = MarketDataWorkerAttempt(
            provider="coinbase",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            attempted_at=attempted_at,
            requested_starts_at=attempted_at - timedelta(hours=3),
            requested_ends_at=attempted_at,
        )
        await state_store.record_attempt(attempt)
        await state_store.record_failure(
            MarketDataWorkerFailure(
                attempt=attempt,
                code="provider_unavailable",
                message="Historical market-data retrieval failed.",
                next_retry_at=attempted_at + timedelta(minutes=5),
            )
        )
        state = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert state is not None
        object.__setattr__(state, "next_retry_at", None)
        service = _StubRangeService([])

        with pytest.raises(MarketDataWorkerError, match="retry deadline"):
            await ingest_once(
                service=service,
                dataset_store=DatasetStore(tmp_path),
                state_store=state_store,
                provider="coinbase",
                product_id="BTC-USD",
                lookback_hours=3,
                now=attempted_at + timedelta(hours=1),
            )

        assert service.requests == []

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


def test_ingest_once_persists_exponential_retry_schedule(tmp_path: Path) -> None:
    """Repeated provider failures durably increase retry delay with a bounded base."""

    async def exercise() -> None:
        first_at = datetime(2026, 7, 29, 2, 17, tzinfo=UTC)
        service = _StubRangeService([RuntimeError("first"), RuntimeError("second")])
        state_store = InMemoryMarketDataWorkerStateStore()
        dataset_store = DatasetStore(tmp_path)

        await ingest_once(
            service=service,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USD",
            lookback_hours=3,
            now=first_at,
            retry_base_seconds=300,
            jitter_factory=lambda: 0.0,
        )
        first = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert first is not None
        assert first.next_retry_at == first_at + timedelta(seconds=300)

        second_at = first_at + timedelta(minutes=5)
        await ingest_once(
            service=service,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USD",
            lookback_hours=3,
            now=second_at,
            retry_base_seconds=300,
            jitter_factory=lambda: 0.0,
        )
        second = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert second is not None
        assert second.next_retry_at == second_at + timedelta(seconds=600)

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


def test_ingest_once_extends_last_verified_dataset_with_one_candle_overlap(tmp_path: Path) -> None:
    """Later cycles fetch only overlap plus missing candles and publish cumulative coverage."""

    async def exercise() -> None:
        first_end = datetime(2026, 7, 29, 2, tzinfo=UTC)
        initial = _report(starts_at=first_end - timedelta(hours=3), candle_count=3)
        incremental = _report(starts_at=first_end - timedelta(hours=1), candle_count=3)
        service = _StubRangeService([initial, incremental])
        state_store = InMemoryMarketDataWorkerStateStore()
        dataset_store = DatasetStore(tmp_path)

        await ingest_once(
            service=service,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USD",
            lookback_hours=3,
            now=first_end + timedelta(minutes=5),
        )
        await ingest_once(
            service=service,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USD",
            lookback_hours=3,
            now=first_end + timedelta(hours=2, minutes=5),
        )

        state = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert state is not None
        assert service.requests[1][1:3] == (
            first_end - timedelta(hours=1),
            first_end + timedelta(hours=2),
        )
        assert state.covered_starts_at == initial.starts_at
        assert state.covered_ends_at == incremental.ends_at
        assert state.expected_candle_count == 5
        assert state.received_candle_count == 5
        assert state.dataset_revision == 2
        assert state.maintenance_kind == "incremental"
        assert state.expected_ends_at == first_end + timedelta(hours=2)
        assert len(tuple((tmp_path / "manifests").glob("*.json"))) == 2

    asyncio.run(exercise())


def test_incremental_revision_reuses_unchanged_day_partitions(tmp_path: Path) -> None:
    """Cumulative hourly updates must not rewrite every historical day on each cycle."""

    async def exercise() -> None:
        first_end = datetime(2026, 7, 29, 2, tzinfo=UTC)
        initial = _report(starts_at=first_end - timedelta(hours=30), candle_count=30)
        incremental = _report(starts_at=first_end - timedelta(hours=1), candle_count=3)
        service = _StubRangeService([initial, incremental])
        state_store = InMemoryMarketDataWorkerStateStore()
        dataset_store = DatasetStore(tmp_path)

        await ingest_once(
            service=service,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USD",
            lookback_hours=30,
            now=first_end + timedelta(minutes=5),
        )
        first_state = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert first_state is not None and first_state.content_fingerprint is not None
        first_manifest = dataset_store.load_verified(
            tmp_path
            / "manifests"
            / f"{first_state.content_fingerprint.removeprefix('sha256:')}.json"
        )

        await ingest_once(
            service=service,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USD",
            lookback_hours=30,
            now=first_end + timedelta(hours=2, minutes=5),
        )
        second_state = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert second_state is not None and second_state.content_fingerprint is not None
        second_manifest = dataset_store.load_verified(
            tmp_path
            / "manifests"
            / f"{second_state.content_fingerprint.removeprefix('sha256:')}.json"
        )

        assert len(set(first_manifest.files) & set(second_manifest.files)) == 2

    asyncio.run(exercise())


def test_ingest_once_does_not_republish_when_dataset_is_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A steady-state cycle performs no provider, publication, or dataset-scan work."""

    async def exercise() -> None:
        ends_at = datetime(2026, 7, 29, 2, tzinfo=UTC)
        report = _report(starts_at=ends_at - timedelta(hours=3), candle_count=3)
        service = _StubRangeService([report])
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

        def fail_load(_fingerprint: str) -> tuple[Candle, ...]:
            raise AssertionError("steady-state no-op must not scan immutable datasets")

        monkeypatch.setattr(dataset_store, "load_candles", fail_load)
        await ingest_once(
            service=service,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USD",
            lookback_hours=3,
            now=ends_at + timedelta(minutes=45),
            verify_current_dataset=False,
        )

        assert len(service.requests) == 1
        assert len(tuple((tmp_path / "manifests").glob("*.json"))) == 1
        state = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert state is not None
        assert state.last_attempt_at == ends_at + timedelta(minutes=45)
        assert state.next_retry_at == ends_at + timedelta(minutes=50)

    asyncio.run(exercise())


def test_market_data_worker_honors_persisted_retry_deadline_before_first_attempt(
    tmp_path: Path,
) -> None:
    """A restarted worker waits for durable backoff before contacting its provider."""

    async def exercise() -> None:
        now = datetime(2026, 7, 29, 2, 18, tzinfo=UTC)
        retry_at = now + timedelta(minutes=29)
        attempt = MarketDataWorkerAttempt(
            provider="coinbase",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            attempted_at=now - timedelta(minutes=1),
            requested_starts_at=now.replace(minute=0) - timedelta(hours=3),
            requested_ends_at=now.replace(minute=0),
        )
        state_store = InMemoryMarketDataWorkerStateStore()
        await state_store.record_attempt(attempt)
        await state_store.record_failure(
            MarketDataWorkerFailure(
                attempt=attempt,
                code="provider_unavailable",
                message="Historical market-data retrieval failed.",
                next_retry_at=retry_at,
            )
        )
        service = _StubRangeService(
            [_report(starts_at=attempt.requested_starts_at, candle_count=3)]
        )
        stop = asyncio.Event()
        task = asyncio.create_task(
            run_market_data_worker(
                stop,
                service=service,
                dataset_store=DatasetStore(tmp_path),
                state_store=state_store,
                provider="coinbase",
                product_id="BTC-USD",
                lookback_hours=3,
                interval_seconds=300,
                now_factory=lambda: now,
            )
        )
        await asyncio.sleep(0.05)
        assert service.requests == []
        stop.set()
        await task

    asyncio.run(exercise())


def test_ingest_once_reconciles_current_state_with_verified_dataset(tmp_path: Path) -> None:
    """A restart must not report current when its authoritative dataset cannot be verified."""

    async def exercise() -> None:
        ends_at = datetime(2026, 7, 29, 2, tzinfo=UTC)
        report = _report(starts_at=ends_at - timedelta(hours=3), candle_count=3)
        service = _StubRangeService([report])
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
        manifest = next((tmp_path / "manifests").glob("*.json"))
        manifest_contents = manifest.read_bytes()
        manifest.unlink()

        await ingest_once(
            service=service,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USD",
            lookback_hours=3,
            now=ends_at + timedelta(minutes=45),
            jitter_factory=lambda: 0.0,
        )

        state = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert state is not None
        assert state.status is MarketDataWorkerStatus.FAILED
        assert state.failure_code == "dataset_verification_failed"
        assert state.covered_ends_at == ends_at
        assert len(service.requests) == 1

        manifest.write_bytes(manifest_contents)
        await ingest_once(
            service=service,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USD",
            lookback_hours=3,
            now=ends_at + timedelta(minutes=50),
            jitter_factory=lambda: 0.0,
        )

        recovered = await state_store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert recovered is not None
        assert recovered.status is MarketDataWorkerStatus.SUCCEEDED
        assert recovered.failure_code is None
        assert recovered.dataset_revision == 1
        assert len(service.requests) == 1

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
            MarketDataWorkerFailure(
                first,
                "provider_unavailable",
                "Retrieval failed.",
                next_retry_at=first.attempted_at + timedelta(minutes=5),
            )
        )
        retry = replace(
            first,
            attempted_at=first.attempted_at + timedelta(minutes=5),
            expected_consecutive_failures=1,
        )
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


def test_late_or_replayed_transitions_cannot_corrupt_authoritative_coverage() -> None:
    """Superseded and replayed transitions cannot regress or duplicate terminal state."""

    async def exercise() -> None:
        store = InMemoryMarketDataWorkerStateStore()
        starts_at = datetime(2026, 7, 29, tzinfo=UTC)
        older = MarketDataWorkerAttempt(
            provider="coinbase",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            attempted_at=starts_at + timedelta(hours=3, minutes=5),
            requested_starts_at=starts_at,
            requested_ends_at=starts_at + timedelta(hours=3),
        )
        newer = replace(
            older,
            attempted_at=starts_at + timedelta(hours=4, minutes=5),
            requested_ends_at=starts_at + timedelta(hours=4),
        )
        newer_success = MarketDataWorkerSuccess(
            attempt=newer,
            covered_starts_at=starts_at,
            covered_ends_at=starts_at + timedelta(hours=4),
            expected_candle_count=4,
            received_candle_count=4,
            gap_count=0,
            missing_intervals=0,
            content_fingerprint="sha256:" + "2" * 64,
        )
        await store.record_attempt(older)
        await store.record_attempt(newer)
        await store.record_success(newer_success)
        await store.record_success(
            MarketDataWorkerSuccess(
                attempt=older,
                covered_starts_at=starts_at,
                covered_ends_at=starts_at + timedelta(hours=3),
                expected_candle_count=3,
                received_candle_count=3,
                gap_count=0,
                missing_intervals=0,
                content_fingerprint="sha256:" + "1" * 64,
            )
        )
        await store.record_success(newer_success)
        await store.record_attempt(newer)
        await store.record_failure(
            MarketDataWorkerFailure(
                attempt=newer,
                code="provider_unavailable",
                message="Historical market-data retrieval failed.",
            )
        )

        final = await store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert final is not None
        assert final.covered_ends_at == starts_at + timedelta(hours=4)
        assert final.content_fingerprint == "sha256:" + "2" * 64
        assert final.dataset_revision == 1
        assert final.status is MarketDataWorkerStatus.SUCCEEDED
        assert final.consecutive_failures == 0

    asyncio.run(exercise())


def test_stale_failure_snapshot_cannot_claim_a_new_attempt() -> None:
    """A worker planned before another failure cannot double-count with stale backoff."""

    async def exercise() -> None:
        store = InMemoryMarketDataWorkerStateStore()
        first_at = datetime(2026, 7, 29, 2, tzinfo=UTC)
        first = MarketDataWorkerAttempt(
            provider="coinbase",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            attempted_at=first_at,
            requested_starts_at=first_at - timedelta(hours=3),
            requested_ends_at=first_at,
        )
        stale_newer = replace(first, attempted_at=first_at + timedelta(seconds=1))

        assert await store.record_attempt(first) is True
        await store.record_failure(
            MarketDataWorkerFailure(
                attempt=first,
                code="provider_unavailable",
                message="Historical market-data retrieval failed.",
                next_retry_at=first_at + timedelta(seconds=300),
            )
        )
        assert await store.record_attempt(stale_newer) is False
        await store.record_failure(
            MarketDataWorkerFailure(
                attempt=stale_newer,
                code="provider_unavailable",
                message="Historical market-data retrieval failed.",
                next_retry_at=stale_newer.attempted_at + timedelta(seconds=300),
            )
        )

        state = await store.get("coinbase", "BTC-USD", CandleInterval.ONE_HOUR)
        assert state is not None
        assert state.status is MarketDataWorkerStatus.FAILED
        assert state.last_attempt_at == first_at
        assert state.consecutive_failures == 1
        assert state.next_retry_at == first_at + timedelta(seconds=300)

    asyncio.run(exercise())


def test_rejected_attempt_claim_stops_before_provider_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale worker whose claim loses compare-and-set performs no external work."""

    async def exercise() -> None:
        service = _StubRangeService([])
        store = InMemoryMarketDataWorkerStateStore()

        async def reject_attempt(attempt: MarketDataWorkerAttempt) -> bool:
            """Simulate a concurrent durable transition invalidating the snapshot."""
            del attempt
            return False

        monkeypatch.setattr(store, "record_attempt", reject_attempt)
        await ingest_once(
            service=service,
            dataset_store=DatasetStore(tmp_path),
            state_store=store,
            provider="coinbase",
            product_id="BTC-USD",
            lookback_hours=3,
            now=datetime(2026, 7, 29, 2, 5, tzinfo=UTC),
        )

        assert service.requests == []
        assert not tuple((tmp_path / "manifests").glob("*.json"))

    asyncio.run(exercise())
