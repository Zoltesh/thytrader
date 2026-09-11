"""Gap inspection uses the full watch lookback and never interpolates missing bars."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.config import Settings
from thytrader.data_control.models import GapCause
from thytrader.data_control.service import inspect_gaps
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import Candle, CandleInterval, CandleRangeReport
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.watchlist import InMemoryMarketDataWatchlistStore, MarketDataWatchTarget
from thytrader.market_data.worker_state import InMemoryMarketDataWorkerStateStore
from thytrader.market_data_worker.service import ingest_once

if TYPE_CHECKING:
    from pathlib import Path


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
        starts_at, ends_at, gaps, warning = await inspect_gaps(
            service=_EmptyProbeService(),
            dataset_store=DatasetStore(tmp_path),
            state_store=InMemoryMarketDataWorkerStateStore(),
            watchlist=watchlist,
            settings=Settings(_env_file=None),
            product_id="ETH-USD",
            timeframe="5m",
            now=now,
        )
        assert ends_at - starts_at == timedelta(hours=720)
        assert (ends_at - starts_at) // CandleInterval.FIVE_MINUTES.duration == 8_640
        assert len(gaps) == 8_640
        assert {gap.cause for gap in gaps} == {GapCause.NOT_FETCHED}
        assert warning is not None

    asyncio.run(exercise())


def test_inspect_gaps_classifies_hole_and_keeps_newest_island_contiguous(
    tmp_path: Path,
) -> None:
    """Older complete days stay local; the skipped day is a classified hole, not a fill."""

    async def exercise() -> None:
        ends_at = datetime(2026, 7, 31, tzinfo=UTC)
        hole = datetime(2026, 7, 29, 12, tzinfo=UTC)
        ingest_service = _CompleteWindowService(missing=frozenset({hole}))
        probe_service = _CompleteWindowService()
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
        assert latest[0].starts_at == "2026-07-30T00:00:00Z"

        starts_at, gap_ends, gaps, _warning = await inspect_gaps(
            service=probe_service,
            dataset_store=dataset_store,
            state_store=state_store,
            watchlist=watchlist,
            settings=Settings(_env_file=None),
            product_id="ETH-USD",
            timeframe="1h",
            now=now,
        )
        gap_starts = {gap.starts_at for gap in gaps}
        assert hole in gap_starts
        assert datetime(2026, 7, 28, tzinfo=UTC) not in gap_starts
        assert datetime(2026, 7, 30, tzinfo=UTC) not in gap_starts
        assert starts_at == datetime(2026, 7, 28, tzinfo=UTC)
        assert gap_ends == ends_at
        assert all(gap.cause is GapCause.NOT_FETCHED for gap in gaps if gap.starts_at == hole)

    asyncio.run(exercise())
