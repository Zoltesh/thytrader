"""Ranged ingest: provider-sized pages, fair budgets, rate-limit backoff, no interpolation.

The fake provider below behaves like Coinbase candles: one request returns at most 350
buckets and a product has no candles before it was listed. Counting its requests is the
evidence for ADR 0085's speedup. The previous worker fetched one UTC day per request,
so a one-year backfill cost 365 requests at 1h and 1d alike, and a watch reaching back
before a product's listing never got past the empty days at its lookback start.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import time
from typing import TYPE_CHECKING

import pytest

from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import (
    HISTORICAL_REQUEST_MAX_CANDLES,
    Candle,
    CandleInterval,
    CandleRangeReport,
    MarketDataRateLimitedError,
)
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.watchlist import MarketDataWatchTarget
from thytrader.market_data.worker_state import (
    InMemoryMarketDataWorkerStateStore,
    MarketDataWorkerStatus,
)
from thytrader.market_data_worker.pacing import ProviderPacer
from thytrader.market_data_worker.service import (
    INGEST_REQUESTS_PER_REQUESTED_TARGET_CYCLE,
    INGEST_REQUESTS_PER_TARGET_CYCLE,
    IngestStop,
    _ingest_due_targets,
    _plan_targets,
    _TargetPriority,
    ingest_once,
    island_covers_watch,
)

if TYPE_CHECKING:
    from pathlib import Path

_CLOSED_END = datetime(2026, 7, 1, tzinfo=UTC)
_NOW = _CLOSED_END + timedelta(minutes=5)
_ONE_YEAR_HOURS = 8_760


class _CoinbaseLikeProvider:
    """Counts requests and serves complete candles from a listing instant onward."""

    def __init__(
        self,
        *,
        listed_at: datetime | None = None,
        missing: frozenset[datetime] = frozenset(),
        throttle_requests: frozenset[int] = frozenset(),
    ) -> None:
        """Configure the listing instant, absent bars, and which request indexes 429."""
        self.requests: list[tuple[str, CandleInterval, datetime, datetime]] = []
        self._listed_at = listed_at
        self._missing = missing
        self._throttle = throttle_requests

    async def get_range(
        self,
        product_id: str,
        timeframe: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Serve one page, refusing anything larger than a Coinbase candle page."""
        index = len(self.requests)
        self.requests.append((product_id, timeframe, starts_at, ends_at))
        if index in self._throttle:
            raise MarketDataRateLimitedError("Coinbase rate-limited a market-data request.")
        buckets = (ends_at - starts_at) // timeframe.duration
        assert 1 <= buckets <= HISTORICAL_REQUEST_MAX_CANDLES, "one request is one page"
        assert (ends_at - starts_at) % timeframe.duration == timedelta(0)
        candles = tuple(
            Candle(
                starts_at=start,
                open=Decimal("100"),
                high=Decimal("110"),
                low=Decimal("90"),
                close=Decimal("105"),
                volume=Decimal("3"),
            )
            for start in (starts_at + timeframe.duration * offset for offset in range(buckets))
            if (self._listed_at is None or start >= self._listed_at) and start not in self._missing
        )
        return analyze_range(candles, timeframe, starts_at, ends_at, now)


def _target(
    product_id: str,
    timeframe: CandleInterval,
    lookback_hours: int,
    *,
    requested_at: datetime | None = None,
) -> MarketDataWatchTarget:
    """Build one enabled watch target, optionally with a pending ingest request."""
    return MarketDataWatchTarget(
        provider="coinbase",
        product_id=product_id,
        timeframe=timeframe,
        lookback_hours=lookback_hours,
        enabled=True,
        updated_at=_NOW,
        ingest_requested_at=requested_at,
    )


def _manifest_count(root: Path) -> int:
    """Count published dataset revisions under one dataset root."""
    return len(tuple((root / "manifests").glob("*.json")))


@pytest.mark.parametrize(
    ("timeframe", "expected_requests", "expected_bars"),
    [
        (CandleInterval.ONE_HOUR, 26, 8_760),
        (CandleInterval.ONE_DAY, 2, 365),
    ],
)
def test_one_year_backfill_request_counts(
    tmp_path: Path,
    timeframe: CandleInterval,
    expected_requests: int,
    expected_bars: int,
) -> None:
    """A one-year backfill costs 26 requests at 1h and 2 at 1d, not 365 UTC-day requests."""

    async def exercise() -> None:
        provider = _CoinbaseLikeProvider()
        state_store = InMemoryMarketDataWorkerStateStore()
        outcome = await ingest_once(
            service=provider,
            dataset_store=DatasetStore(tmp_path),
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USDC",
            lookback_hours=_ONE_YEAR_HOURS,
            now=_NOW,
            timeframe=timeframe,
        )

        assert outcome.stop is IngestStop.COMPLETE
        assert len(provider.requests) == expected_requests
        assert outcome.requests == expected_requests
        state = await state_store.get("coinbase", "BTC-USDC", timeframe)
        assert state is not None
        assert state.status is MarketDataWorkerStatus.SUCCEEDED
        assert state.covered_starts_at == _CLOSED_END - timedelta(hours=_ONE_YEAR_HOURS)
        assert state.covered_ends_at == _CLOSED_END
        assert state.expected_candle_count == expected_bars
        assert state.history_floor_at is None
        assert _manifest_count(tmp_path) == 1, "one cumulative revision per walk"
        # Newest page first: coverage reached the newest closed bar on the first request.
        assert provider.requests[0][3] == _CLOSED_END

    asyncio.run(exercise())


def test_long_watch_on_a_young_product_stops_at_the_listing_floor(tmp_path: Path) -> None:
    """A five-year 1h watch on a one-year-old product needs 27 requests, then records its floor.

    The previous oldest-first walk, budgeted at two UTC days per cycle, re-probed the same two
    empty pre-listing days every cycle and never published anything.
    """

    async def exercise() -> None:
        listed_at = _CLOSED_END - timedelta(hours=_ONE_YEAR_HOURS)
        provider = _CoinbaseLikeProvider(listed_at=listed_at)
        state_store = InMemoryMarketDataWorkerStateStore()
        outcome = await ingest_once(
            service=provider,
            dataset_store=DatasetStore(tmp_path),
            state_store=state_store,
            provider="coinbase",
            product_id="NEW-USDC",
            lookback_hours=5 * _ONE_YEAR_HOURS,
            now=_NOW,
        )

        assert outcome.stop is IngestStop.HOLE
        assert len(provider.requests) == 27, "25 full pages, then the listing page twice"
        state = await state_store.get("coinbase", "NEW-USDC", CandleInterval.ONE_HOUR)
        assert state is not None
        assert state.covered_starts_at == listed_at
        assert state.history_floor_at == listed_at
        assert state.expected_candle_count == _ONE_YEAR_HOURS
        assert island_covers_watch(
            covered_starts_at=state.covered_starts_at,
            covered_ends_at=state.covered_ends_at,
            island_complete=state.complete,
            lookback_hours=5 * _ONE_YEAR_HOURS,
            interval=CandleInterval.ONE_HOUR,
            closed_end=_CLOSED_END,
            history_floor_at=state.history_floor_at,
        )

    asyncio.run(exercise())


def test_request_budget_splits_a_backfill_across_cycles_with_one_publish_each(
    tmp_path: Path,
) -> None:
    """A requested target's per-cycle budget bounds requests; each cycle publishes once."""

    async def exercise() -> None:
        provider = _CoinbaseLikeProvider()
        state_store = InMemoryMarketDataWorkerStateStore()
        dataset_store = DatasetStore(tmp_path)
        first = await ingest_once(
            service=provider,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USDC",
            lookback_hours=_ONE_YEAR_HOURS,
            now=_NOW,
            max_requests=INGEST_REQUESTS_PER_REQUESTED_TARGET_CYCLE,
        )
        assert first.stop is IngestStop.BUDGET
        assert first.requests == INGEST_REQUESTS_PER_REQUESTED_TARGET_CYCLE
        assert _manifest_count(tmp_path) == 1

        second = await ingest_once(
            service=provider,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USDC",
            lookback_hours=_ONE_YEAR_HOURS,
            now=_NOW + timedelta(seconds=1),
            skip_reconcile=True,
            max_requests=INGEST_REQUESTS_PER_REQUESTED_TARGET_CYCLE,
        )
        assert second.stop is IngestStop.COMPLETE
        state = await state_store.get("coinbase", "BTC-USDC", CandleInterval.ONE_HOUR)
        assert state is not None
        assert state.maintenance_kind == "prefix_backfill"
        assert state.covered_starts_at == _CLOSED_END - timedelta(hours=_ONE_YEAR_HOURS)
        assert _manifest_count(tmp_path) == 2
        # Prefix pages carry one overlap bar, so 26 or 27 requests cover the year.
        assert len(provider.requests) in {26, 27}

    asyncio.run(exercise())


def test_rate_limit_mid_walk_publishes_progress_and_backs_off(tmp_path: Path) -> None:
    """A 429 ends the walk, keeps the pages already fetched, and sets a shared cooldown."""

    async def exercise() -> None:
        provider = _CoinbaseLikeProvider(throttle_requests=frozenset({2}))
        state_store = InMemoryMarketDataWorkerStateStore()
        clock = [100.0]
        pacer = ProviderPacer(clock=lambda: clock[0])
        outcome = await ingest_once(
            service=provider,
            dataset_store=DatasetStore(tmp_path),
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USDC",
            lookback_hours=_ONE_YEAR_HOURS,
            now=_NOW,
            pacer=pacer,
        )

        assert outcome.stop is IngestStop.RATE_LIMITED
        assert len(provider.requests) == 3
        assert pacer.throttled == 1
        assert pacer.cooldown_seconds == pytest.approx(2.0)
        state = await state_store.get("coinbase", "BTC-USDC", CandleInterval.ONE_HOUR)
        assert state is not None
        assert state.status is MarketDataWorkerStatus.SUCCEEDED
        assert state.expected_candle_count == 2 * HISTORICAL_REQUEST_MAX_CANDLES
        assert state.covered_ends_at == _CLOSED_END

    asyncio.run(exercise())


def test_rate_limit_on_first_request_records_a_short_retry(tmp_path: Path) -> None:
    """A throttle before any progress is a redacted failure that retries after the cooldown."""

    async def exercise() -> None:
        provider = _CoinbaseLikeProvider(throttle_requests=frozenset({0}))
        state_store = InMemoryMarketDataWorkerStateStore()
        outcome = await ingest_once(
            service=provider,
            dataset_store=DatasetStore(tmp_path),
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USDC",
            lookback_hours=24,
            now=_NOW,
            pacer=ProviderPacer(clock=lambda: 0.0),
        )

        assert outcome.stop is IngestStop.RATE_LIMITED
        state = await state_store.get("coinbase", "BTC-USDC", CandleInterval.ONE_HOUR)
        assert state is not None
        assert state.status is MarketDataWorkerStatus.FAILED
        assert state.failure_code == "provider_rate_limited"
        assert state.next_retry_at == _NOW + timedelta(seconds=2)
        assert not (tmp_path / "manifests").exists()

    asyncio.run(exercise())


def test_pacer_doubles_the_cooldown_and_resets_after_success() -> None:
    """Consecutive throttles back off exponentially up to the ceiling; success clears it."""
    clock = [0.0]
    pacer = ProviderPacer(
        pause_seconds=0.25,
        cooldown_initial_seconds=2.0,
        cooldown_max_seconds=5.0,
        clock=lambda: clock[0],
    )

    assert pacer.throttled_by_provider() == pytest.approx(2.0)
    assert pacer.throttled_by_provider() == pytest.approx(4.0)
    assert pacer.throttled_by_provider() == pytest.approx(5.0)
    pacer.completed()
    assert pacer.cooldown_seconds == 0.0
    assert pacer.throttled_by_provider() == pytest.approx(2.0)


def test_pacer_spaces_requests_by_the_pause() -> None:
    """The next request slot opens only after the configured pause."""

    async def exercise() -> float:
        pacer = ProviderPacer(pause_seconds=0.05)
        assert await pacer.acquire() is True
        pacer.completed()
        started = time.monotonic()
        assert await pacer.acquire() is True
        return time.monotonic() - started

    assert asyncio.run(exercise()) >= 0.04


def test_pacer_cooldown_wait_yields_to_shutdown() -> None:
    """A long rate-limit cooldown must not delay a graceful stop."""

    async def exercise() -> tuple[bool, float]:
        stop = asyncio.Event()
        pacer = ProviderPacer(stop_requested=stop, cooldown_initial_seconds=30.0)
        pacer.throttled_by_provider()
        asyncio.get_running_loop().call_later(0.05, stop.set)
        started = time.monotonic()
        granted = await pacer.acquire()
        return granted, time.monotonic() - started

    granted, waited = asyncio.run(exercise())
    assert granted is False
    assert waited < 5.0


async def _seed_island(
    root: Path,
    state_store: InMemoryMarketDataWorkerStateStore,
    *,
    timeframe: CandleInterval,
    lookback_hours: int,
    now: datetime,
) -> DatasetStore:
    """Publish one complete island ending at ``now``'s closed bar."""
    dataset_store = DatasetStore(root)
    await ingest_once(
        service=_CoinbaseLikeProvider(),
        dataset_store=dataset_store,
        state_store=state_store,
        provider="coinbase",
        product_id="ETH-USDC",
        lookback_hours=lookback_hours,
        now=now,
        timeframe=timeframe,
    )
    return dataset_store


def test_forward_walk_restarts_the_island_after_a_settled_hole(tmp_path: Path) -> None:
    """A confirmed hole older than the settle window starts a newer island with its floor."""

    async def exercise() -> None:
        state_store = InMemoryMarketDataWorkerStateStore()
        dataset_store = await _seed_island(
            tmp_path, state_store, timeframe=CandleInterval.ONE_HOUR, lookback_hours=24, now=_NOW
        )
        later = _NOW + timedelta(hours=10)
        hole = _CLOSED_END + timedelta(hours=2)
        provider = _CoinbaseLikeProvider(missing=frozenset({hole}))
        outcome = await ingest_once(
            service=provider,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="ETH-USDC",
            lookback_hours=24,
            now=later,
        )

        assert outcome.stop is IngestStop.COMPLETE
        assert len(provider.requests) == 2, "one page plus one confirmation"
        state = await state_store.get("coinbase", "ETH-USDC", CandleInterval.ONE_HOUR)
        assert state is not None
        assert state.covered_starts_at == hole + timedelta(hours=1)
        assert state.history_floor_at == hole + timedelta(hours=1)
        assert state.covered_ends_at == _CLOSED_END + timedelta(hours=10)
        candles = dataset_store.load_candles(state.content_fingerprint or "")
        assert all(candle.starts_at != hole for candle in candles)

    asyncio.run(exercise())


def test_forward_walk_waits_for_a_late_newest_bar_instead_of_discarding_the_island(
    tmp_path: Path,
) -> None:
    """An unsettled hole near the newest bar extends the island up to it and stops."""

    async def exercise() -> None:
        state_store = InMemoryMarketDataWorkerStateStore()
        dataset_store = await _seed_island(
            tmp_path,
            state_store,
            timeframe=CandleInterval.FIVE_MINUTES,
            lookback_hours=24,
            now=_NOW,
        )
        later = _CLOSED_END + timedelta(hours=1, minutes=1)
        closed_end = later.replace(minute=0, second=0)
        late = closed_end - timedelta(minutes=10)
        provider = _CoinbaseLikeProvider(missing=frozenset({late}))
        outcome = await ingest_once(
            service=provider,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="ETH-USDC",
            lookback_hours=24,
            now=later,
            timeframe=CandleInterval.FIVE_MINUTES,
        )

        assert outcome.stop is IngestStop.HOLE
        assert len(provider.requests) == 1, "an unsettled hole is not re-fetched"
        state = await state_store.get("coinbase", "ETH-USDC", CandleInterval.FIVE_MINUTES)
        assert state is not None
        seeded_end = CandleInterval.FIVE_MINUTES.align_closed_end(_NOW)
        assert state.covered_starts_at == seeded_end - timedelta(hours=24)
        assert state.covered_ends_at == late
        assert state.history_floor_at is None

        caught_up = _CoinbaseLikeProvider()
        await ingest_once(
            service=caught_up,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="coinbase",
            product_id="ETH-USDC",
            lookback_hours=24,
            now=later + timedelta(seconds=30),
            timeframe=CandleInterval.FIVE_MINUTES,
        )
        recovered = await state_store.get("coinbase", "ETH-USDC", CandleInterval.FIVE_MINUTES)
        assert recovered is not None
        assert recovered.covered_ends_at == closed_end
        assert recovered.covered_starts_at == seeded_end - timedelta(hours=24)

    asyncio.run(exercise())


def test_initial_backfill_ends_before_an_unpublished_newest_bar(tmp_path: Path) -> None:
    """A missing newest bar trims the new island without becoming a provider floor."""

    async def exercise() -> None:
        newest = _CLOSED_END - timedelta(hours=1)
        provider = _CoinbaseLikeProvider(missing=frozenset({newest}))
        state_store = InMemoryMarketDataWorkerStateStore()
        await ingest_once(
            service=provider,
            dataset_store=DatasetStore(tmp_path),
            state_store=state_store,
            provider="coinbase",
            product_id="BTC-USDC",
            lookback_hours=24,
            now=_NOW,
        )

        state = await state_store.get("coinbase", "BTC-USDC", CandleInterval.ONE_HOUR)
        assert state is not None
        assert state.covered_starts_at == _CLOSED_END - timedelta(hours=24)
        assert state.covered_ends_at == newest
        assert state.history_floor_at is None
        assert len(provider.requests) == 1

    asyncio.run(exercise())


def test_cycle_plan_orders_maintenance_then_requested_then_backfill(tmp_path: Path) -> None:
    """Covered watches go first, then pending requests (oldest first), then other backfill."""

    async def exercise() -> None:
        state_store = InMemoryMarketDataWorkerStateStore()
        await _seed_island(
            tmp_path, state_store, timeframe=CandleInterval.ONE_HOUR, lookback_hours=24, now=_NOW
        )
        covered = _target("ETH-USDC", CandleInterval.ONE_HOUR, 24)
        backfill = _target("SOL-USDC", CandleInterval.ONE_HOUR, 24)
        newer_request = _target(
            "BTC-USDC", CandleInterval.ONE_DAY, 24, requested_at=_NOW - timedelta(minutes=1)
        )
        older_request = _target(
            "ADA-USDC", CandleInterval.ONE_DAY, 24, requested_at=_NOW - timedelta(minutes=9)
        )
        plans = await _plan_targets(
            (backfill, newer_request, covered, older_request), state_store, _NOW
        )

        assert [plan.target.product_id for plan in plans] == [
            "ETH-USDC",
            "ADA-USDC",
            "BTC-USDC",
            "SOL-USDC",
        ]
        assert [plan.priority for plan in plans] == [
            _TargetPriority.MAINTENANCE,
            _TargetPriority.REQUESTED,
            _TargetPriority.REQUESTED,
            _TargetPriority.BACKFILL,
        ]
        assert plans[1].request_budget == INGEST_REQUESTS_PER_REQUESTED_TARGET_CYCLE
        assert plans[3].request_budget == INGEST_REQUESTS_PER_TARGET_CYCLE

    asyncio.run(exercise())


def test_cycle_gives_every_target_a_bounded_share_and_skips_idle_wait(tmp_path: Path) -> None:
    """One long requested backfill cannot starve another target; leftover work loops at once."""

    async def exercise() -> None:
        provider = _CoinbaseLikeProvider()
        state_store = InMemoryMarketDataWorkerStateStore()
        dataset_store = DatasetStore(tmp_path)
        long_request = _target(
            "BTC-USDC", CandleInterval.ONE_HOUR, _ONE_YEAR_HOURS, requested_at=_NOW
        )
        short_backfill = _target("ETH-USDC", CandleInterval.ONE_DAY, _ONE_YEAR_HOURS)
        wait = await _ingest_due_targets(
            (short_backfill, long_request),
            service=provider,
            dataset_store=dataset_store,
            state_store=state_store,
            interval_seconds=300,
            cycle_now=_NOW,
            verified_targets=set(),
            stop_requested=asyncio.Event(),
        )

        assert wait is None, "a target ran out of budget, so the next cycle starts now"
        per_target = [request[0] for request in provider.requests]
        assert per_target.count("BTC-USDC") == INGEST_REQUESTS_PER_REQUESTED_TARGET_CYCLE
        assert per_target.count("ETH-USDC") == 2
        assert per_target[0] == "BTC-USDC", "the requested target is served first"
        daily = await state_store.get("coinbase", "ETH-USDC", CandleInterval.ONE_DAY)
        assert daily is not None
        assert daily.expected_candle_count == 365

        next_wait = await _ingest_due_targets(
            (short_backfill, long_request),
            service=provider,
            dataset_store=dataset_store,
            state_store=state_store,
            interval_seconds=300,
            cycle_now=_NOW + timedelta(seconds=1),
            verified_targets={("BTC-USDC", CandleInterval.ONE_HOUR)},
            stop_requested=asyncio.Event(),
        )
        assert isinstance(next_wait, int)
        hourly = await state_store.get("coinbase", "BTC-USDC", CandleInterval.ONE_HOUR)
        assert hourly is not None
        assert hourly.expected_candle_count == _ONE_YEAR_HOURS

    asyncio.run(exercise())
