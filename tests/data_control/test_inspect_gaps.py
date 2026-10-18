"""Gap inspection uses the full watch lookback and never interpolates missing bars."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import threading
from typing import TYPE_CHECKING

from thytrader.config import Settings
from thytrader.data_control import service as service_module
from thytrader.data_control.models import GapCause
from thytrader.data_control.service import INSPECT_GAPS_MAX_BARS, _local_starts, inspect_gaps
from thytrader.market_data.dataset_manifest import DatasetStoreError
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import Candle, CandleInterval, CandleRangeReport
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.watchlist import InMemoryMarketDataWatchlistStore, MarketDataWatchTarget
from thytrader.market_data.worker_state import InMemoryMarketDataWorkerStateStore
from thytrader.market_data_worker.service import ingest_once

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from thytrader.market_data.dataset_manifest import DatasetManifest


class _EmptyProbeService:
    """Range stub that fails closed so inspect-gaps classifies without a heavy fetch."""

    async def get_range(
        self,
        product_id: str,
        timeframe: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Reject the probe so missing bars stay classified, not synthesized."""
        del product_id, timeframe, starts_at, ends_at, now
        raise RuntimeError("probe disabled")


class _CompleteWindowService:
    """Return complete candles for the requested window, omitting configured holes."""

    def __init__(self, *, missing: frozenset[datetime] = frozenset()) -> None:
        self._missing = missing

    async def get_range(
        self,
        product_id: str,
        timeframe: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Build an exact complete-or-gapped report for the requested bounds."""
        del product_id, now
        count = int((ends_at - starts_at) / timeframe.duration)
        candles = tuple(
            Candle(
                starts_at=starts_at + timeframe.duration * index,
                open=Decimal("100"),
                high=Decimal("110"),
                low=Decimal("90"),
                close=Decimal("105"),
                volume=Decimal("12.5"),
            )
            for index in range(count)
            if starts_at + timeframe.duration * index not in self._missing
        )
        return analyze_range(candles, timeframe, starts_at, ends_at, now=ends_at)


def test_inspect_gaps_five_minute_thirty_day_lookback_is_not_clipped(tmp_path: Path) -> None:
    """A 720-hour 5m watch must classify the full 8,640-bar window, not a 14-day clip."""

    async def exercise() -> None:
        now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        watchlist = InMemoryMarketDataWatchlistStore()
        await watchlist.upsert(
            MarketDataWatchTarget(
                provider="demo",
                product_id="ETH-USD",
                timeframe=CandleInterval.FIVE_MINUTES,
                lookback_hours=720,
                enabled=True,
                updated_at=now,
            )
        )
        inspection = await inspect_gaps(
            service=_EmptyProbeService(),
            dataset_store=DatasetStore(tmp_path),
            state_store=InMemoryMarketDataWorkerStateStore(),
            watchlist=watchlist,
            settings=Settings(_env_file=None),
            product_id="ETH-USD",
            timeframe="5m",
            now=now,
        )
        assert inspection.ends_at - inspection.starts_at == timedelta(hours=720)
        assert (
            inspection.ends_at - inspection.starts_at
        ) // CandleInterval.FIVE_MINUTES.duration == 8_640
        assert inspection.gap_summary["not_fetched"] == 8_640
        assert len(inspection.gaps) <= 200
        assert {gap.cause for gap in inspection.gaps} == {GapCause.NOT_FETCHED}
        assert inspection.warning is not None
        assert inspection.complete is False
        assert inspection.watch_complete is False
        assert inspection.lookback_hours == 720

    asyncio.run(exercise())


def test_inspect_gaps_fifteen_minute_thirty_day_lookback_is_not_clipped(tmp_path: Path) -> None:
    """A 720-hour 15m watch must classify the full 2,880-bar window."""

    async def exercise() -> None:
        now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        watchlist = InMemoryMarketDataWatchlistStore()
        await watchlist.upsert(
            MarketDataWatchTarget(
                provider="demo",
                product_id="ETH-USD",
                timeframe=CandleInterval.FIFTEEN_MINUTES,
                lookback_hours=720,
                enabled=True,
                updated_at=now,
            )
        )
        inspection = await inspect_gaps(
            service=_EmptyProbeService(),
            dataset_store=DatasetStore(tmp_path),
            state_store=InMemoryMarketDataWorkerStateStore(),
            watchlist=watchlist,
            settings=Settings(_env_file=None),
            product_id="ETH-USD",
            timeframe="15m",
            now=now,
        )
        assert inspection.ends_at - inspection.starts_at == timedelta(hours=720)
        assert (
            inspection.ends_at - inspection.starts_at
        ) // CandleInterval.FIFTEEN_MINUTES.duration == 2_880
        assert inspection.gap_summary["not_fetched"] == 2_880
        assert {gap.cause for gap in inspection.gaps} == {GapCause.NOT_FETCHED}
        assert inspection.warning is not None

    asyncio.run(exercise())


def test_inspect_gaps_thirty_minute_thirty_day_lookback_is_not_clipped(tmp_path: Path) -> None:
    """A 720-hour 30m watch must classify the full 1,440-bar window."""

    async def exercise() -> None:
        now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        watchlist = InMemoryMarketDataWatchlistStore()
        await watchlist.upsert(
            MarketDataWatchTarget(
                provider="demo",
                product_id="ETH-USD",
                timeframe=CandleInterval.THIRTY_MINUTES,
                lookback_hours=720,
                enabled=True,
                updated_at=now,
            )
        )
        inspection = await inspect_gaps(
            service=_EmptyProbeService(),
            dataset_store=DatasetStore(tmp_path),
            state_store=InMemoryMarketDataWorkerStateStore(),
            watchlist=watchlist,
            settings=Settings(_env_file=None),
            product_id="ETH-USD",
            timeframe="30m",
            now=now,
        )
        assert inspection.ends_at - inspection.starts_at == timedelta(hours=720)
        assert (
            inspection.ends_at - inspection.starts_at
        ) // CandleInterval.THIRTY_MINUTES.duration == 1_440
        assert inspection.gap_summary["not_fetched"] == 1_440
        assert {gap.cause for gap in inspection.gaps} == {GapCause.NOT_FETCHED}
        assert inspection.warning is not None

    asyncio.run(exercise())


def test_inspect_gaps_six_hour_thirty_day_lookback_is_not_clipped(tmp_path: Path) -> None:
    """A 720-hour 6h watch must classify the full 120-bar window."""

    async def exercise() -> None:
        now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        watchlist = InMemoryMarketDataWatchlistStore()
        await watchlist.upsert(
            MarketDataWatchTarget(
                provider="demo",
                product_id="ETH-USD",
                timeframe=CandleInterval.SIX_HOURS,
                lookback_hours=720,
                enabled=True,
                updated_at=now,
            )
        )
        inspection = await inspect_gaps(
            service=_EmptyProbeService(),
            dataset_store=DatasetStore(tmp_path),
            state_store=InMemoryMarketDataWorkerStateStore(),
            watchlist=watchlist,
            settings=Settings(_env_file=None),
            product_id="ETH-USD",
            timeframe="6h",
            now=now,
        )
        assert inspection.ends_at - inspection.starts_at == timedelta(hours=720)
        assert (
            inspection.ends_at - inspection.starts_at
        ) // CandleInterval.SIX_HOURS.duration == 120
        assert inspection.gap_summary["not_fetched"] == 120
        assert {gap.cause for gap in inspection.gaps} == {GapCause.NOT_FETCHED}
        assert inspection.warning is not None
        assert inspection.complete is False
        assert inspection.watch_complete is False
        assert inspection.lookback_hours == 720

    asyncio.run(exercise())


def test_inspect_gaps_one_day_thirty_day_lookback_is_not_clipped(tmp_path: Path) -> None:
    """A 720-hour 1d watch must classify the full 30-bar window."""

    async def exercise() -> None:
        now = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
        watchlist = InMemoryMarketDataWatchlistStore()
        await watchlist.upsert(
            MarketDataWatchTarget(
                provider="demo",
                product_id="ETH-USD",
                timeframe=CandleInterval.ONE_DAY,
                lookback_hours=720,
                enabled=True,
                updated_at=now,
            )
        )
        inspection = await inspect_gaps(
            service=_EmptyProbeService(),
            dataset_store=DatasetStore(tmp_path),
            state_store=InMemoryMarketDataWorkerStateStore(),
            watchlist=watchlist,
            settings=Settings(_env_file=None),
            product_id="ETH-USD",
            timeframe="1d",
            now=now,
        )
        assert inspection.ends_at - inspection.starts_at == timedelta(hours=720)
        assert (inspection.ends_at - inspection.starts_at) // CandleInterval.ONE_DAY.duration == 30
        assert inspection.gap_summary["not_fetched"] == 30
        assert {gap.cause for gap in inspection.gaps} == {GapCause.NOT_FETCHED}
        assert inspection.warning is not None

    asyncio.run(exercise())


def test_inspect_gaps_one_minute_two_hour_lookback_is_not_clipped(tmp_path: Path) -> None:
    """A 2-hour 1m watch must classify the full 120-bar window."""

    async def exercise() -> None:
        now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
        watchlist = InMemoryMarketDataWatchlistStore()
        await watchlist.upsert(
            MarketDataWatchTarget(
                provider="demo",
                product_id="ETH-USD",
                timeframe=CandleInterval.ONE_MINUTE,
                lookback_hours=2,
                enabled=True,
                updated_at=now,
            )
        )
        inspection = await inspect_gaps(
            service=_EmptyProbeService(),
            dataset_store=DatasetStore(tmp_path),
            state_store=InMemoryMarketDataWorkerStateStore(),
            watchlist=watchlist,
            settings=Settings(_env_file=None),
            product_id="ETH-USD",
            timeframe="1m",
            now=now,
        )
        assert inspection.ends_at - inspection.starts_at == timedelta(hours=2)
        assert (
            inspection.ends_at - inspection.starts_at
        ) // CandleInterval.ONE_MINUTE.duration == 120
        assert inspection.gap_summary["not_fetched"] == 120
        assert {gap.cause for gap in inspection.gaps} == {GapCause.NOT_FETCHED}
        assert inspection.warning is not None

    asyncio.run(exercise())


def test_inspect_gaps_two_hour_thirty_day_lookback_is_not_clipped(tmp_path: Path) -> None:
    """A 720-hour 2h watch must classify the full 360-bar window."""

    async def exercise() -> None:
        now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
        watchlist = InMemoryMarketDataWatchlistStore()
        await watchlist.upsert(
            MarketDataWatchTarget(
                provider="demo",
                product_id="ETH-USD",
                timeframe=CandleInterval.TWO_HOURS,
                lookback_hours=720,
                enabled=True,
                updated_at=now,
            )
        )
        inspection = await inspect_gaps(
            service=_EmptyProbeService(),
            dataset_store=DatasetStore(tmp_path),
            state_store=InMemoryMarketDataWorkerStateStore(),
            watchlist=watchlist,
            settings=Settings(_env_file=None),
            product_id="ETH-USD",
            timeframe="2h",
            now=now,
        )
        assert inspection.ends_at - inspection.starts_at == timedelta(hours=720)
        assert (
            inspection.ends_at - inspection.starts_at
        ) // CandleInterval.TWO_HOURS.duration == 360
        assert inspection.gap_summary["not_fetched"] == 360
        assert {gap.cause for gap in inspection.gaps} == {GapCause.NOT_FETCHED}
        assert inspection.warning is not None

    asyncio.run(exercise())


def test_inspect_gaps_four_hour_thirty_day_lookback_is_not_clipped(tmp_path: Path) -> None:
    """A 720-hour 4h watch must classify the full 180-bar window."""

    async def exercise() -> None:
        now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
        watchlist = InMemoryMarketDataWatchlistStore()
        await watchlist.upsert(
            MarketDataWatchTarget(
                provider="demo",
                product_id="ETH-USD",
                timeframe=CandleInterval.FOUR_HOURS,
                lookback_hours=720,
                enabled=True,
                updated_at=now,
            )
        )
        inspection = await inspect_gaps(
            service=_EmptyProbeService(),
            dataset_store=DatasetStore(tmp_path),
            state_store=InMemoryMarketDataWorkerStateStore(),
            watchlist=watchlist,
            settings=Settings(_env_file=None),
            product_id="ETH-USD",
            timeframe="4h",
            now=now,
        )
        assert inspection.ends_at - inspection.starts_at == timedelta(hours=720)
        assert (
            inspection.ends_at - inspection.starts_at
        ) // CandleInterval.FOUR_HOURS.duration == 180
        assert inspection.gap_summary["not_fetched"] == 180
        assert {gap.cause for gap in inspection.gaps} == {GapCause.NOT_FETCHED}
        assert inspection.warning is not None

    asyncio.run(exercise())


def test_inspect_gaps_reports_a_no_trade_bar_as_published_not_as_a_gap(
    tmp_path: Path,
) -> None:
    """A confirmed interval without trades is a flat published bar (ADR 0095), not a gap.

    Before ADR 0095 the newest island started after the missing bar and the older bars of the
    watch were reported as gaps behind a floor.
    """

    async def exercise() -> None:
        ends_at = datetime(2026, 7, 31, tzinfo=UTC)
        hole = datetime(2026, 7, 29, 12, tzinfo=UTC)
        ingest_service = _CompleteWindowService(missing=frozenset({hole}))
        probe_service = _CompleteWindowService(missing=frozenset({hole}))
        dataset_store = DatasetStore(tmp_path)
        state_store = InMemoryMarketDataWorkerStateStore()
        watchlist = InMemoryMarketDataWatchlistStore()
        now = ends_at + timedelta(minutes=5)
        await watchlist.upsert(
            MarketDataWatchTarget(
                provider="demo",
                product_id="ETH-USD",
                timeframe=CandleInterval.ONE_HOUR,
                lookback_hours=72,
                enabled=True,
                updated_at=now,
            )
        )
        await ingest_once(
            service=ingest_service,
            dataset_store=dataset_store,
            state_store=state_store,
            provider="demo",
            product_id="ETH-USD",
            lookback_hours=72,
            now=now,
        )
        latest = dataset_store.list_latest_verified()
        assert len(latest) == 1
        assert latest[0].complete is True
        assert latest[0].starts_at == "2026-07-28T00:00:00Z"
        assert latest[0].synthetic_no_trade_intervals == 1

        inspection = await inspect_gaps(
            service=probe_service,
            dataset_store=dataset_store,
            state_store=state_store,
            watchlist=watchlist,
            settings=Settings(_env_file=None),
            product_id="ETH-USD",
            timeframe="1h",
            now=now,
        )
        assert inspection.gaps == ()
        assert inspection.watch_complete is True
        assert inspection.complete is True
        assert inspection.island_complete is True
        assert inspection.starts_at == datetime(2026, 7, 28, tzinfo=UTC)
        assert inspection.ends_at == ends_at

    asyncio.run(exercise())


def test_inspect_gaps_truncates_when_bar_budget_is_exhausted(tmp_path: Path) -> None:
    """Server-side row budgets return a partial gap_summary instead of walking the watch."""

    async def exercise() -> None:
        now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        watchlist = InMemoryMarketDataWatchlistStore()
        await watchlist.upsert(
            MarketDataWatchTarget(
                provider="demo",
                product_id="DOGE-USD",
                timeframe=CandleInterval.ONE_MINUTE,
                lookback_hours=2160,
                enabled=True,
                updated_at=now,
            )
        )
        inspection = await inspect_gaps(
            service=_EmptyProbeService(),
            dataset_store=DatasetStore(tmp_path),
            state_store=InMemoryMarketDataWorkerStateStore(),
            watchlist=watchlist,
            settings=Settings(_env_file=None),
            product_id="DOGE-USD",
            timeframe="1m",
            now=now,
            max_bars=50,
            max_probe_days=1,
            time_budget_seconds=2.0,
        )
        assert inspection.truncated is True
        assert inspection.scanned_bar_count == 50
        assert sum(inspection.gap_summary.values()) <= 50
        assert inspection.warning is not None
        assert "budget" in inspection.warning

    asyncio.run(exercise())


def test_inspect_gaps_one_minute_watch_truncates_under_default_budgets(tmp_path: Path) -> None:
    """Production 1m 2160h budgets must fail closed instead of classifying 129,600 bars."""

    async def exercise() -> None:
        now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        watchlist = InMemoryMarketDataWatchlistStore()
        await watchlist.upsert(
            MarketDataWatchTarget(
                provider="demo",
                product_id="DOGE-USD",
                timeframe=CandleInterval.ONE_MINUTE,
                lookback_hours=2160,
                enabled=True,
                updated_at=now,
            )
        )
        inspection = await inspect_gaps(
            service=_EmptyProbeService(),
            dataset_store=DatasetStore(tmp_path),
            state_store=InMemoryMarketDataWorkerStateStore(),
            watchlist=watchlist,
            settings=Settings(_env_file=None),
            product_id="DOGE-USD",
            timeframe="1m",
            now=now,
        )
        assert inspection.truncated is True
        assert inspection.scanned_bar_count == INSPECT_GAPS_MAX_BARS
        assert sum(inspection.gap_summary.values()) <= INSPECT_GAPS_MAX_BARS
        assert inspection.watch_complete is False

    asyncio.run(exercise())


def _bars(start: datetime, count: int) -> tuple[Candle, ...]:
    """Consecutive 2h bars from ``start``."""
    return tuple(
        Candle(
            starts_at=start + timedelta(hours=2 * index),
            open=Decimal(1),
            high=Decimal(1),
            low=Decimal(1),
            close=Decimal(1),
            volume=Decimal(1),
        )
        for index in range(count)
    )


def _publish(store: DatasetStore, product_id: str, candles: tuple[Candle, ...]) -> None:
    """Write one complete 2h island."""
    interval = CandleInterval.TWO_HOURS
    start, ends = candles[0].starts_at, candles[-1].starts_at + interval.duration
    store.write("coinbase", product_id, analyze_range(candles, interval, start, ends, now=ends))


class _NoFullListing(DatasetStore):
    """Fail if anything re-verifies every revision of every market."""

    def list_verified(self) -> tuple[DatasetManifest, ...]:
        raise AssertionError("inspect-gaps must not re-verify the whole dataset store")


def test_local_starts_union_this_markets_islands_without_a_full_listing(tmp_path: Path) -> None:
    """Every verified island of the market counts (independent islands included); others don't."""
    store = DatasetStore(tmp_path)
    start = datetime(2026, 10, 1, tzinfo=UTC)
    older, newer = _bars(start, 2), _bars(start + timedelta(hours=6), 2)
    _publish(store, "BTC-USD", older)
    _publish(store, "BTC-USD", newer)
    _publish(store, "ETH-USD", _bars(start, 4))
    guarded = _NoFullListing(tmp_path)
    starts, truncated = _local_starts(
        guarded, "coinbase", "BTC-USD", CandleInterval.TWO_HOURS, max_candles=100
    )
    assert starts == {candle.starts_at for candle in (*older, *newer)}
    assert not truncated
    capped, truncated = _local_starts(
        guarded, "coinbase", "BTC-USD", CandleInterval.TWO_HOURS, max_candles=3
    )
    assert len(capped) == 3
    assert truncated


def test_list_verified_for_matches_the_filtered_full_listing(tmp_path: Path) -> None:
    """The per-market listing is exactly the full listing restricted to that market."""
    store = DatasetStore(tmp_path)
    start = datetime(2026, 10, 1, tzinfo=UTC)
    _publish(store, "BTC-USD", _bars(start, 3))
    _publish(store, "BTC-USD", _bars(start, 5))
    _publish(store, "ETH-USD", _bars(start, 2))
    full = {
        item.content_fingerprint
        for item in store.list_verified()
        if (item.provider, item.product_id, item.timeframe) == ("coinbase", "BTC-USD", "2h")
    }
    scoped = store.list_verified_for("coinbase", "BTC-USD", "2h")
    assert {item.content_fingerprint for item in scoped} == full
    assert len(full) == 2


def test_inspect_gaps_reads_datasets_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blocking dataset reads run in a worker thread, so the API stays responsive."""
    threads: list[str] = []

    def record(*args: object, **kwargs: object) -> tuple[set[datetime], bool]:
        del args, kwargs
        threads.append(threading.current_thread().name)
        return set(), False

    monkeypatch.setattr(service_module, "_local_starts", record)
    now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
    asyncio.run(
        inspect_gaps(
            service=_EmptyProbeService(),
            dataset_store=DatasetStore(tmp_path),
            state_store=InMemoryMarketDataWorkerStateStore(),
            watchlist=InMemoryMarketDataWatchlistStore(),
            settings=Settings(_env_file=None),
            product_id="ETH-USD",
            timeframe="2h",
            now=now,
        )
    )
    assert threads
    assert threads[0] != threading.main_thread().name


def test_list_verified_for_falls_back_past_a_revision_that_fails_deep_verification(
    tmp_path: Path,
) -> None:
    """A newest revision whose content fails verification is skipped; older ones still count."""
    start = datetime(2026, 10, 1, tzinfo=UTC)
    store = DatasetStore(tmp_path)
    _publish(store, "BTC-USD", _bars(start, 2))
    _publish(store, "BTC-USD", _bars(start, 4))
    newest = max(
        store.list_verified_for("coinbase", "BTC-USD", "2h"), key=lambda item: item.ends_at
    )

    class _Corrupt(DatasetStore):
        def load_verified(self, manifest_path: Path) -> DatasetManifest:
            if manifest_path.stem == newest.content_fingerprint.removeprefix("sha256:"):
                raise DatasetStoreError("synthetic content corruption")
            return super().load_verified(manifest_path)

    cold = _Corrupt(tmp_path)
    remaining = cold.list_verified_for("coinbase", "BTC-USD", "2h")
    assert [item.content_fingerprint for item in remaining] != []
    assert newest.content_fingerprint not in {item.content_fingerprint for item in remaining}


def test_stale_cached_metadata_never_misattributes_another_market(tmp_path: Path) -> None:
    """A stale cached candidate neither hides a market's revision nor admits it to another."""
    start = datetime(2026, 10, 1, tzinfo=UTC)
    store = DatasetStore(tmp_path)
    _publish(store, "ETH-USD", _bars(start, 3))
    store.list_latest_verified()
    (path,) = list((tmp_path / "manifests").glob("*.json"))
    stamp, candidate = store._candidate_cache[path]
    store._candidate_cache[path] = (stamp, replace(candidate, product_id="BTC-USD"))
    assert store.list_verified_for("coinbase", "BTC-USD", "2h") == ()
    assert len(store.list_verified_for("coinbase", "ETH-USD", "2h")) == 1
    store.list_latest_verified()
    assert len(store.list_verified_for("coinbase", "ETH-USD", "2h")) == 1


def test_list_verified_for_prunes_deleted_manifests_from_its_caches(tmp_path: Path) -> None:
    """Repeated scoped listings do not keep entries for manifests that are gone."""
    start = datetime(2026, 10, 1, tzinfo=UTC)
    store = DatasetStore(tmp_path)
    _publish(store, "BTC-USD", _bars(start, 2))
    assert len(store.list_verified_for("coinbase", "BTC-USD", "2h")) == 1
    (path,) = list((tmp_path / "manifests").glob("*.json"))
    store.list_latest_verified()
    assert path in store._candidate_cache
    path.unlink()
    assert store.list_verified_for("coinbase", "BTC-USD", "2h") == ()
    assert path not in store._candidate_cache
    assert path not in store._verified_cache


def test_a_malformed_unrelated_manifest_never_aborts_a_scoped_listing(tmp_path: Path) -> None:
    """Deeply nested JSON (RecursionError on parse) is skipped, as in the full listing."""
    start = datetime(2026, 10, 1, tzinfo=UTC)
    store = DatasetStore(tmp_path)
    _publish(store, "BTC-USD", _bars(start, 2))
    nested = "[" * 200_000 + "]" * 200_000
    (tmp_path / "manifests" / ("f" * 64 + ".json")).write_text(nested)
    assert len(store.list_verified_for("coinbase", "BTC-USD", "2h")) == 1
