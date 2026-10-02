"""Sparse markets keep their history: no-trade bars, listing-only floors (ADR 0095).

Coinbase returns no candle for an interval without trades. The worker used to treat such
an interior gap as a provider hole: it republished only the newest contiguous island and
recorded that island's start as ``history_floor_at``, which also blocked backfill. A
90-day BONK-USD 1m watch kept two candles. These tests drive the worker against a
Coinbase-shaped fake whose quiet intervals return nothing.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from typing import TYPE_CHECKING

from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import (
    HISTORICAL_REQUEST_MAX_CANDLES,
    Candle,
    CandleInterval,
    CandleRangeReport,
)
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.watchlist import MarketDataWatchTarget
from thytrader.market_data.worker_state import InMemoryMarketDataWorkerStateStore
from thytrader.market_data_worker.service import (
    IngestStop,
    _ingest_due_targets,
    bounded_lookback_start,
    ingest_once,
    island_covers_watch,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_MINUTE = CandleInterval.ONE_MINUTE
_HOUR = CandleInterval.ONE_HOUR
_DAY = CandleInterval.ONE_DAY


def _price(start: datetime, interval: CandleInterval) -> Decimal:
    """Return a deterministic, varying price for one bar so flat bars are checkable."""
    index = int(start.timestamp()) // int(interval.duration.total_seconds())
    return Decimal(100 + index % 23)


class _SparseProvider:
    """Coinbase-shaped fake: one page per request, nothing for quiet bars or pre-listing."""

    def __init__(
        self,
        *,
        quiet: Callable[[datetime], bool] = lambda _start: False,
        listed_at: datetime | None = None,
    ) -> None:
        """Configure which bar starts had no trades and when the market listed."""
        self.requests: list[tuple[CandleInterval, datetime, datetime]] = []
        self._quiet = quiet
        self._listed_at = listed_at

    async def get_range(
        self,
        product_id: str,
        timeframe: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Serve one page; a daily bar exists when any minute of that day traded."""
        del product_id
        self.requests.append((timeframe, starts_at, ends_at))
        buckets = (ends_at - starts_at) // timeframe.duration
        assert 1 <= buckets <= HISTORICAL_REQUEST_MAX_CANDLES, "one request is one page"
        candles = tuple(
            self._candle(start, timeframe)
            for start in (starts_at + timeframe.duration * offset for offset in range(buckets))
            if self._traded(start, timeframe)
        )
        return analyze_range(candles, timeframe, starts_at, ends_at, now)

    def _traded(self, start: datetime, timeframe: CandleInterval) -> bool:
        """True when the bucket holds at least one trade."""
        if timeframe is _DAY:
            minute = start
            while minute < start + _DAY.duration:
                if self._traded(minute, _MINUTE):
                    return True
                minute += timedelta(minutes=30)
            return False
        listed = self._listed_at is None or start >= self._listed_at
        return listed and not self._quiet(start)

    @staticmethod
    def _candle(start: datetime, timeframe: CandleInterval) -> Candle:
        """Build one traded bar whose close varies bar to bar."""
        close = _price(start, timeframe)
        return Candle(
            starts_at=start,
            open=close - Decimal(1),
            high=close + Decimal(2),
            low=close - Decimal(2),
            close=close,
            volume=Decimal("7"),
        )


def _quiet_pattern(start: datetime) -> bool:
    """Leave about four in ten minutes without trades, like a thin memecoin book."""
    return (int(start.timestamp()) // 60) % 10 in {1, 2, 5, 8}


async def _ingest(
    provider: _SparseProvider,
    store: DatasetStore,
    state: InMemoryMarketDataWorkerStateStore,
    *,
    now: datetime,
    lookback_hours: int,
    timeframe: CandleInterval = _MINUTE,
    product_id: str = "BONK-USD",
    max_requests: int | None = None,
) -> IngestStop:
    """Run one ``ingest_once`` against the fake and return its stop."""
    outcome = await ingest_once(
        service=provider,
        dataset_store=store,
        state_store=state,
        provider="coinbase",
        product_id=product_id,
        lookback_hours=lookback_hours,
        now=now,
        timeframe=timeframe,
        max_requests=max_requests,
    )
    return outcome.stop


def test_quiet_minutes_keep_a_sparse_series_whole(tmp_path: Path) -> None:
    """A thin 1m market spans its lookback; quiet minutes are flat no-trade bars, no floor."""

    async def exercise() -> None:
        now = datetime(2026, 10, 2, 11, 13, 30, tzinfo=UTC)
        closed_end = _MINUTE.align_closed_end(now)
        provider = _SparseProvider(quiet=_quiet_pattern)
        store = DatasetStore(tmp_path)
        state_store = InMemoryMarketDataWorkerStateStore()

        await _ingest(provider, store, state_store, now=now, lookback_hours=6)

        state = await state_store.get("coinbase", "BONK-USD", _MINUTE)
        assert state is not None
        lookback_start = bounded_lookback_start(closed_end, 6, _MINUTE)
        assert state.covered_starts_at == lookback_start
        assert state.history_floor_at is None
        assert island_covers_watch(
            covered_starts_at=state.covered_starts_at,
            covered_ends_at=state.covered_ends_at,
            island_complete=state.complete,
            lookback_hours=6,
            interval=_MINUTE,
            closed_end=closed_end,
            history_floor_at=state.history_floor_at,
        )
        candles = store.load_candles(state.content_fingerprint or "")
        previous: Candle | None = None
        for candle in candles:
            if _quiet_pattern(candle.starts_at):
                assert candle.volume == 0
                assert candle.open == candle.high == candle.low == candle.close
                assert previous is not None
                assert candle.close == previous.close, "flat at the previous close"
            else:
                assert candle.volume == Decimal("7")
            previous = candle
        manifest = store.load_manifest(state.content_fingerprint or "")
        assert manifest.synthetic_no_trade_intervals == sum(
            1 for candle in candles if candle.volume == 0
        )
        assert manifest.synthetic_no_trade_intervals > 100

        # Later cycles keep the start; quiet minutes never move or set a floor.
        for minutes in (7, 23, 61):
            later = now + timedelta(minutes=minutes)
            await _ingest(provider, store, state_store, now=later, lookback_hours=6)
            extended = await state_store.get("coinbase", "BONK-USD", _MINUTE)
            assert extended is not None
            assert extended.covered_starts_at == lookback_start
            assert extended.history_floor_at is None

    asyncio.run(exercise())


def test_sparse_forward_chunk_keeps_history_and_never_moves_the_floor(tmp_path: Path) -> None:
    """The BONK forward page (21 bars, 12 traded) extends the island; nothing restarts it.

    Before ADR 0095 this page restarted the island at 10:57 and recorded that floor.
    """

    async def exercise() -> None:
        store = DatasetStore(tmp_path)
        state_store = InMemoryMarketDataWorkerStateStore()
        seeded_at = datetime(2026, 10, 2, 10, 52, 30, tzinfo=UTC)
        await _ingest(_SparseProvider(), store, state_store, now=seeded_at, lookback_hours=2)
        seeded = await state_store.get("coinbase", "BONK-USD", _MINUTE)
        assert seeded is not None
        assert seeded.covered_ends_at == datetime(2026, 10, 2, 10, 52, tzinfo=UTC)

        quiet = {
            datetime(2026, 10, 2, 10, minute, tzinfo=UTC) for minute in (52, 53, 54, 55, 56)
        } | {datetime(2026, 10, 2, 11, minute, tzinfo=UTC) for minute in (0, 1, 5, 12)}
        provider = _SparseProvider(quiet=lambda start: start in quiet)
        now = datetime(2026, 10, 2, 11, 13, 30, tzinfo=UTC)
        stop = await _ingest(provider, store, state_store, now=now, lookback_hours=2)

        assert stop is IngestStop.UNSETTLED, "11:00 is inside the settle window"
        assert provider.requests[0][1:] == (
            datetime(2026, 10, 2, 10, 51, tzinfo=UTC),
            datetime(2026, 10, 2, 11, 13, tzinfo=UTC),
        )
        state = await state_store.get("coinbase", "BONK-USD", _MINUTE)
        assert state is not None
        assert state.covered_starts_at == seeded.covered_starts_at
        assert state.history_floor_at is None
        assert state.covered_ends_at == datetime(2026, 10, 2, 11, 0, tzinfo=UTC)

        settled = now + timedelta(minutes=20)
        await _ingest(provider, store, state_store, now=settled, lookback_hours=2)
        later = await state_store.get("coinbase", "BONK-USD", _MINUTE)
        assert later is not None
        assert later.covered_starts_at == seeded.covered_starts_at
        assert later.history_floor_at is None
        assert later.covered_ends_at is not None
        assert later.covered_ends_at >= datetime(2026, 10, 2, 11, 13, tzinfo=UTC)

    asyncio.run(exercise())


def test_backward_walk_past_the_listing_records_a_floor(tmp_path: Path) -> None:
    """A product listed mid-lookback gets a floor only after the listing search proves it.

    The walk passes the listing, pages to the UTC day boundary, then probes daily candles
    back to 350 days past the 90-day 1m ceiling. Quiet minutes after the listing are still
    flat bars.
    """

    async def exercise() -> None:
        listed_at = datetime(2026, 10, 1, 10, 57, tzinfo=UTC)
        now = datetime(2026, 10, 2, 11, 13, 30, tzinfo=UTC)
        closed_end = _MINUTE.align_closed_end(now)
        provider = _SparseProvider(quiet=_quiet_pattern, listed_at=listed_at)
        store = DatasetStore(tmp_path)
        state_store = InMemoryMarketDataWorkerStateStore()

        stop = await _ingest(provider, store, state_store, now=now, lookback_hours=48)

        assert stop is IngestStop.LISTING_FLOOR
        state = await state_store.get("coinbase", "BONK-USD", _MINUTE)
        assert state is not None
        assert state.covered_starts_at == listed_at
        assert state.history_floor_at == listed_at
        assert island_covers_watch(
            covered_starts_at=state.covered_starts_at,
            covered_ends_at=state.covered_ends_at,
            island_complete=state.complete,
            lookback_hours=48,
            interval=_MINUTE,
            closed_end=closed_end,
            history_floor_at=state.history_floor_at,
        )
        daily = [request for request in provider.requests if request[0] is _DAY]
        assert len(daily) == 4, "two daily probe pages, each confirmed once"
        day_start = datetime(2026, 10, 1, tzinfo=UTC)
        # Newest first: 350 days below the listing day, then down to 2025-07-19, which is
        # 350 days before the UTC day holding the 90-day ceiling (2026-07-04 11:13).
        assert daily[0][1:] == (datetime(2025, 10, 16, tzinfo=UTC), day_start)
        assert daily[-1][1] == datetime(2025, 7, 19, tzinfo=UTC)
        minute_pages = [request for request in provider.requests if request[0] is _MINUTE]
        assert all(request[1] >= day_start for request in minute_pages)

    asyncio.run(exercise())


def test_forward_gap_never_moves_a_listing_floor(tmp_path: Path) -> None:
    """After a listing floor, quiet forward minutes become flat bars; the floor stays put."""

    async def exercise() -> None:
        listed_at = datetime(2026, 10, 1, 10, 57, tzinfo=UTC)
        store = DatasetStore(tmp_path)
        state_store = InMemoryMarketDataWorkerStateStore()
        now = datetime(2026, 10, 2, 11, 13, 30, tzinfo=UTC)
        listed = _SparseProvider(listed_at=listed_at)
        await _ingest(listed, store, state_store, now=now, lookback_hours=48)

        quiet = _SparseProvider(
            listed_at=listed_at,
            quiet=lambda start: start >= datetime(2026, 10, 2, 11, 13, tzinfo=UTC),
        )
        later = now + timedelta(minutes=40)
        await _ingest(quiet, store, state_store, now=later, lookback_hours=48)

        state = await state_store.get("coinbase", "BONK-USD", _MINUTE)
        assert state is not None
        assert state.covered_starts_at == listed_at
        assert state.history_floor_at == listed_at
        assert state.covered_ends_at == _MINUTE.align_closed_end(later) - timedelta(minutes=15)
        assert all(request[0] is _MINUTE for request in quiet.requests), "no listing re-probe"
        manifest = store.load_manifest(state.content_fingerprint or "")
        assert manifest.synthetic_no_trade_intervals == 25

    asyncio.run(exercise())


def test_quiet_days_inside_the_lookback_are_skipped_with_daily_probes(tmp_path: Path) -> None:
    """Two days without a trade cost a few probes, not dozens of empty minute pages."""

    async def exercise() -> None:
        dead_from = datetime(2026, 9, 29, 6, tzinfo=UTC)
        dead_until = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
        provider = _SparseProvider(quiet=lambda start: dead_from <= start < dead_until)
        store = DatasetStore(tmp_path)
        state_store = InMemoryMarketDataWorkerStateStore()
        now = datetime(2026, 10, 1, 12, 0, 30, tzinfo=UTC)

        await _ingest(provider, store, state_store, now=now, lookback_hours=96)

        state = await state_store.get("coinbase", "BONK-USD", _MINUTE)
        assert state is not None
        assert state.covered_starts_at == bounded_lookback_start(
            _MINUTE.align_closed_end(now), 96, _MINUTE
        )
        assert state.history_floor_at is None
        candles = store.load_candles(state.content_fingerprint or "")
        dead = [candle for candle in candles if dead_from <= candle.starts_at < dead_until]
        last_trade = next(c for c in reversed(candles) if c.starts_at < dead_from)
        assert len(dead) == int((dead_until - dead_from) / _MINUTE.duration)
        assert all(candle.volume == 0 and candle.close == last_trade.close for candle in dead)
        empty_minute_pages = int((dead_until - dead_from) / (_MINUTE.duration * 350))
        assert empty_minute_pages >= 8
        assert len(provider.requests) < 40, "daily probes skipped the dead days"

    asyncio.run(exercise())


def test_lookback_start_without_trades_is_priced_by_the_newest_earlier_trade(
    tmp_path: Path,
) -> None:
    """Quiet bars at the lookback start are flat at the last trade before it; no floor."""

    async def exercise() -> None:
        now = datetime(2026, 10, 2, 12, 5, tzinfo=UTC)
        closed_end = _HOUR.align_closed_end(now)
        lookback_start = closed_end - timedelta(hours=24)
        quiet = {lookback_start, lookback_start + timedelta(hours=1)}
        provider = _SparseProvider(quiet=lambda start: start in quiet)
        store = DatasetStore(tmp_path)
        state_store = InMemoryMarketDataWorkerStateStore()

        stop = await _ingest(
            provider, store, state_store, now=now, lookback_hours=24, timeframe=_HOUR
        )

        assert stop is IngestStop.COMPLETE
        state = await state_store.get("coinbase", "BONK-USD", _HOUR)
        assert state is not None
        assert state.covered_starts_at == lookback_start
        assert state.history_floor_at is None
        candles = store.load_candles(state.content_fingerprint or "")
        before = _price(lookback_start - timedelta(hours=1), _HOUR)
        assert [(c.close, c.volume) for c in candles[:2]] == [(before, 0), (before, 0)]
        assert candles[2].volume == Decimal("7")

    asyncio.run(exercise())


def _watch(product_id: str, lookback_hours: int) -> MarketDataWatchTarget:
    """Build one enabled 1m watch target."""
    return MarketDataWatchTarget(
        provider="coinbase",
        product_id=product_id,
        timeframe=_MINUTE,
        lookback_hours=lookback_hours,
        enabled=True,
        updated_at=datetime(2026, 10, 2, tzinfo=UTC),
    )


async def _cycle(
    provider: _SparseProvider,
    store: DatasetStore,
    state_store: InMemoryMarketDataWorkerStateStore,
    *,
    target: MarketDataWatchTarget,
    now: datetime,
    proven_floors: set[tuple[str, CandleInterval]],
) -> None:
    """Run one worker cycle over a single target."""
    await _ingest_due_targets(
        (target,),
        service=provider,
        dataset_store=store,
        state_store=state_store,
        interval_seconds=60,
        cycle_now=now,
        verified_targets=set(),
        stop_requested=asyncio.Event(),
        proven_floors=proven_floors,
    )


def test_a_recorded_floor_is_proven_again_once_per_worker_process(tmp_path: Path) -> None:
    """Repair: a floor an older worker wrote over real history is cleared on the first visit.

    The 0059 migration clears every pre-ADR 0095 floor; this covers a floor recorded
    after it (by an old worker still running during a deploy). A genuine listing floor is
    re-proven once and then trusted for the rest of the process.
    """

    async def exercise() -> None:
        store = DatasetStore(tmp_path)
        state_store = InMemoryMarketDataWorkerStateStore()
        bogus_floor = datetime(2026, 10, 2, 10, 57, tzinfo=UTC)
        now = datetime(2026, 10, 2, 11, 13, 30, tzinfo=UTC)
        # A floor at 10:57 over a market that traded all along, as the old worker wrote it.
        await _ingest(
            _SparseProvider(listed_at=bogus_floor), store, state_store, now=now, lookback_hours=4
        )
        pinned = await state_store.get("coinbase", "BONK-USD", _MINUTE)
        assert pinned is not None
        assert pinned.history_floor_at == bogus_floor

        trading = _SparseProvider(quiet=_quiet_pattern)
        proven: set[tuple[str, CandleInterval]] = set()
        # Past the 300-second spacing a direct ingest_once schedules after success.
        later = now + timedelta(minutes=6)
        target = _watch("BONK-USD", 4)
        await _cycle(trading, store, state_store, target=target, now=later, proven_floors=proven)
        repaired = await state_store.get("coinbase", "BONK-USD", _MINUTE)
        assert repaired is not None
        assert repaired.history_floor_at is None
        assert repaired.covered_starts_at is not None
        assert repaired.covered_starts_at < bogus_floor

        listing = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
        young = _SparseProvider(listed_at=listing)
        await _ingest(young, store, state_store, now=now, lookback_hours=4, product_id="NEW-USD")
        floors: set[tuple[str, CandleInterval]] = set()
        target = _watch("NEW-USD", 4)
        young.requests.clear()
        await _cycle(young, store, state_store, target=target, now=later, proven_floors=floors)
        assert any(request[0] is _DAY for request in young.requests), "re-proven once"
        assert ("NEW-USD", _MINUTE) in floors
        young.requests.clear()
        await _cycle(
            young,
            store,
            state_store,
            target=target,
            now=later + timedelta(minutes=6),
            proven_floors=floors,
        )
        assert not any(request[0] is _DAY for request in young.requests), "trusted afterwards"
        state = await state_store.get("coinbase", "NEW-USD", _MINUTE)
        assert state is not None
        assert state.history_floor_at == listing

    asyncio.run(exercise())


class _LiquidProvider:
    """Every bar trades; prices vary deterministically (the pre-ADR 0095 baseline fixture)."""

    async def get_range(
        self,
        product_id: str,
        timeframe: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Return every bar in the page."""
        del product_id
        count = (ends_at - starts_at) // timeframe.duration
        candles: list[Candle] = []
        for offset in range(count):
            start = starts_at + timeframe.duration * offset
            index = int(start.timestamp()) // int(timeframe.duration.total_seconds())
            base = Decimal(100 + index % 17)
            candles.append(
                Candle(
                    starts_at=start,
                    open=base,
                    high=base + Decimal("2.5"),
                    low=base - Decimal("1.25"),
                    close=base + Decimal("0.5"),
                    volume=Decimal(1 + index % 5),
                )
            )
        return analyze_range(tuple(candles), timeframe, starts_at, ends_at, now)


# Fingerprints the pre-ADR 0095 worker published for these fixtures (recorded on 1689eba).
_BASELINE = {
    _MINUTE: (
        "sha256:b78f7b26682eb22876c013c17e56d9fcb7db96103c4ecf5ca3d72a4b867dc192",
        "sha256:5c7bf15c15682e985f129fd3a7cf22325c95b9d1924e2ff6f0c2adddb467841a",
        3,
        90,
    ),
    _HOUR: (
        "sha256:bad75afcc985e2cf59942e011f6f9a551395fb7e9884c668416a6ce5a9a547a5",
        "sha256:8d9ecac68c820aab8176270f99a1bc1c8f760ce57e294117e310fc5c7cb1c283",
        24 * 5,
        60 * 7,
    ),
    _DAY: (
        "sha256:ba4f805b7a269a353c6959ef4a752c6b2f3766dd4ab80cfcda82c183b73da457",
        "sha256:3823c0f20ebc107cae39f2e1aee12bb29133a14d818b2aae35b3f65addf620cc",
        24 * 100,
        60 * 24 * 3,
    ),
}


def test_liquid_gap_free_series_keep_their_exact_fingerprints(tmp_path: Path) -> None:
    """Backfill plus a forward extension of a liquid series is byte-identical to before."""

    async def exercise(timeframe: CandleInterval) -> None:
        first_expected, second_expected, lookback_hours, later_minutes = _BASELINE[timeframe]
        root = tmp_path / timeframe.value
        store = DatasetStore(root)
        state_store = InMemoryMarketDataWorkerStateStore()
        now = datetime(2026, 7, 1, 0, 1, tzinfo=UTC)
        for at, expected in (
            (now, first_expected),
            (now + timedelta(minutes=later_minutes), second_expected),
        ):
            await ingest_once(
                service=_LiquidProvider(),
                dataset_store=store,
                state_store=state_store,
                provider="coinbase",
                product_id="BTC-USDC",
                lookback_hours=lookback_hours,
                now=at,
                timeframe=timeframe,
                max_candles_per_request=40,
            )
            state = await state_store.get("coinbase", "BTC-USDC", timeframe)
            assert state is not None
            assert state.content_fingerprint == expected
            manifest_path = root / "manifests" / f"{expected.removeprefix('sha256:')}.json"
            payload = json.loads(manifest_path.read_text())
            assert "synthetic_no_trade_intervals" not in payload

    for timeframe in (_MINUTE, _HOUR, _DAY):
        asyncio.run(exercise(timeframe))


def test_daily_listing_search_finishes_under_one_cycle_budget(tmp_path: Path) -> None:
    """A 1d listing search pages by day within its allowance, so it never repeats forever."""

    async def exercise() -> None:
        listed_at = datetime(2024, 10, 2, tzinfo=UTC)
        now = datetime(2026, 10, 2, 0, 5, tzinfo=UTC)
        provider = _SparseProvider(listed_at=listed_at)
        store = DatasetStore(tmp_path)
        state_store = InMemoryMarketDataWorkerStateStore()

        stop = await _ingest(
            provider,
            store,
            state_store,
            now=now,
            lookback_hours=87_600,
            timeframe=_DAY,
            max_requests=8,
        )

        assert stop is IngestStop.LISTING_FLOOR
        state = await state_store.get("coinbase", "BONK-USD", _DAY)
        assert state is not None
        assert state.covered_starts_at == listed_at
        assert state.history_floor_at == listed_at
        assert 8 < len(provider.requests) <= 8 + 48

    asyncio.run(exercise())


def test_a_quiet_first_bar_at_the_ceiling_is_priced_not_floored(tmp_path: Path) -> None:
    """A watch at the ceiling whose first bar had no trades is not mistaken for a listing."""

    async def exercise() -> None:
        now = datetime(2026, 10, 2, 0, 5, tzinfo=UTC)
        closed_end = _DAY.align_closed_end(now)
        lookback_start = bounded_lookback_start(closed_end, 87_600, _DAY)
        provider = _SparseProvider(
            quiet=lambda start: lookback_start <= start < lookback_start + _DAY.duration
        )
        store = DatasetStore(tmp_path)
        state_store = InMemoryMarketDataWorkerStateStore()

        stop = await _ingest(
            provider, store, state_store, now=now, lookback_hours=87_600, timeframe=_DAY
        )

        assert stop is IngestStop.COMPLETE
        state = await state_store.get("coinbase", "BONK-USD", _DAY)
        assert state is not None
        assert state.covered_starts_at == lookback_start
        assert state.history_floor_at is None
        first = store.load_edge_candle(state.content_fingerprint or "", newest=False)
        assert first.volume == 0
        assert first.close == _price(lookback_start - _DAY.duration, _DAY)

    asyncio.run(exercise())
