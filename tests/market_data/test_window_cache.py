"""Hermetic fixed-seed window, mutable publication tail and bounded rebuild regressions."""

from __future__ import annotations

import asyncio
from bisect import bisect_left
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from thytrader.evaluation.indicators import calculate_indicator_rows
from thytrader.exchanges.coinbase_market_data import CoinbaseMarketDataError
from thytrader.market_data import window_cache
from thytrader.market_data.models import (
    MAX_HISTORICAL_INTERVAL_COUNT,
    Candle,
    CandleInterval,
    CandleRangeReport,
)
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.window_cache import (
    RANGE_BLOCK_INTERVALS,
    DeployWindowCache,
    WindowCacheWarmingError,
)
from thytrader.strategies.models import IndicatorDefinition

pytestmark = pytest.mark.anyio
_START = datetime(2026, 1, 1, tzinfo=UTC)
_MINUTE = timedelta(minutes=1)


def _candle(index: int, *, close: Decimal | None = None) -> Candle:
    """Build a real deterministic Decimal candle, never zero-volume demo evidence."""
    price = close if close is not None else Decimal(100 + index % 97)
    return Candle(
        starts_at=_START + index * _MINUTE,
        open=price,
        high=price + Decimal(1),
        low=price - Decimal("0.5"),
        close=price,
        volume=Decimal(1),
    )


class _Provider:
    """Fake range provider with adapter bounds, omissions, corrections and recorded calls."""

    def __init__(self, count: int, *, omitted: frozenset[int] = frozenset()) -> None:
        """Index fixed provider candles and mutable omissions without network access."""
        self.candles = {_candle(index).starts_at: _candle(index) for index in range(count)}
        self.starts = tuple(self.candles)
        self.omitted = {_START + index * _MINUTE for index in omitted}
        self.calls: list[tuple[datetime, datetime]] = []
        self.fail_after: int | None = None

    def revise(self, index: int, close: Decimal) -> None:
        """Correct one real provider candle before the next fetch."""
        candle = _candle(index, close=close)
        self.candles[candle.starts_at] = candle

    async def fetch(self, starts_at: datetime, ends_at: datetime) -> CandleRangeReport:
        """Validate the fake request exactly at the range adapter boundary."""
        now = datetime.now(UTC)
        count = (ends_at - starts_at) // _MINUTE
        if count > MAX_HISTORICAL_INTERVAL_COUNT or ends_at > now:
            raise CoinbaseMarketDataError("Historical range exceeds the adapter request bound.")
        self.calls.append((starts_at, ends_at))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise ConnectionError("provider unavailable")
        candles = tuple(
            self.candles[start]
            for start in self.starts[
                bisect_left(self.starts, starts_at) : bisect_left(self.starts, ends_at)
            ]
            if start not in self.omitted
        )
        return analyze_range(candles, CandleInterval.ONE_MINUTE, starts_at, ends_at, now)


async def _load(
    cache: DeployWindowCache,
    provider: _Provider,
    *,
    through: int = 9,
    now: datetime | None = None,
    product_id: str = "BTC-USD",
) -> tuple[Candle, ...]:
    """Load an inclusive as-of window at a fixed independent observation time."""
    return await cache.closed_window(
        provider.fetch,
        product_id=product_id,
        interval=CandleInterval.ONE_MINUTE,
        starts_at=_START,
        last_closed_start=_START + through * _MINUTE,
        now=now if now is not None else _START + (through + 4) * _MINUTE,
    )


async def _eventually(
    cache: DeployWindowCache, provider: _Provider, *, through: int = 9
) -> tuple[Candle, ...]:
    """Finish a bounded scan, checking every call's budget without interpreting it as a gap."""
    for _attempt in range(150):
        before = len(provider.calls)
        try:
            result = await _load(cache, provider, through=through)
        except WindowCacheWarmingError as error:
            assert error.range_requests == len(provider.calls) - before
            assert error.range_requests <= window_cache.MAX_RANGE_REQUESTS_PER_CYCLE
            assert error.starts_at == _START
            assert error.scanned_through < error.requested_end
        else:
            assert len(provider.calls) - before <= window_cache.MAX_RANGE_REQUESTS_PER_CYCLE
            return result
    pytest.fail("bounded history scan did not finish")


async def test_cached_window_matches_the_uncached_loader_without_gaps() -> None:
    """A complete source comes back candle-for-candle from settled prefix plus fresh head."""
    provider = _Provider(10)
    window = await _load(DeployWindowCache(), provider)
    assert window == tuple(provider.candles.values())
    assert len(provider.calls) == 2
    assert provider.calls[-1] == (_START + 8 * _MINUTE, _START + 10 * _MINUTE)


async def test_confirmed_interior_gap_becomes_one_no_trade_bar() -> None:
    """A settled bar missing on both observations becomes flat at the previous real close."""
    provider = _Provider(10, omitted=frozenset({5}))
    window = await _load(DeployWindowCache(), provider)
    assert len(window) == 10
    assert window[5].starts_at == _START + 5 * _MINUTE
    assert window[5].volume == 0
    assert window[5].open == window[5].high == window[5].low == window[5].close == window[4].close
    assert len(provider.calls) == 3


async def test_late_published_bar_survives_confirmation() -> None:
    """The second observation heals the settled omission with a real traded candle."""
    provider = _Provider(10, omitted=frozenset({5}))

    async def fetch(starts_at: datetime, ends_at: datetime) -> CandleRangeReport:
        """Publish the omitted bar after the initial range response."""
        report = await provider.fetch(starts_at, ends_at)
        provider.omitted.clear()
        return report

    window = await DeployWindowCache().closed_window(
        fetch,
        product_id="BTC-USD",
        interval=CandleInterval.ONE_MINUTE,
        starts_at=_START,
        last_closed_start=_START + 9 * _MINUTE,
        now=_START + 14 * _MINUTE,
    )
    assert window[5] == _candle(5)


async def test_missing_newest_bar_is_never_fabricated() -> None:
    """A head with no following provider evidence remains absent, even after settling."""
    provider = _Provider(10, omitted=frozenset({9}))
    window = await _load(DeployWindowCache(), provider)
    assert window[-1].starts_at == _START + 8 * _MINUTE
    assert all(candle.volume > 0 for candle in window)


async def test_current_closed_tail_is_corrected_each_same_end_cycle() -> None:
    """Close + 120s has not passed; both tail bars reflect new provider corrections."""
    provider = _Provider(10)
    cache = DeployWindowCache()
    now = _START + 10 * _MINUTE + timedelta(seconds=30)
    first = await _load(cache, provider, now=now)
    before = len(provider.calls)
    provider.revise(8, Decimal(888))
    provider.revise(9, Decimal(999))
    second = await _load(cache, provider, now=now + timedelta(seconds=10))
    assert second[:8] == first[:8]
    assert second[8].close == Decimal(888)
    assert second[9].close == Decimal(999)
    assert provider.calls[before:] == [(_START + 7 * _MINUTE, _START + 10 * _MINUTE)]


async def test_stale_tail_does_not_hide_a_newest_gap_on_the_same_end() -> None:
    """A bar that disappears after being served is not reused from a prior tail response."""
    provider = _Provider(10)
    cache = DeployWindowCache()
    now = _START + 10 * _MINUTE + timedelta(seconds=30)
    first = await _load(cache, provider, now=now)
    provider.omitted.add(first[-1].starts_at)
    second = await _load(cache, provider, now=now)
    assert second[-1].starts_at == _START + 8 * _MINUTE
    assert second == first[:-1]


async def test_mutable_interior_hole_is_not_synthesized_until_settled() -> None:
    """A recently closed interior bar may still publish; a later settled confirmation fills it."""
    provider = _Provider(10, omitted=frozenset({8}))
    cache = DeployWindowCache()
    now = _START + 10 * _MINUTE + timedelta(seconds=30)
    unsettled = await _load(cache, provider, now=now)
    assert all(candle.volume > 0 for candle in unsettled)
    assert len(unsettled) == 9
    settled = await _load(cache, provider, now=now + timedelta(minutes=3))
    assert len(settled) == 10
    assert settled[8].volume == 0
    assert settled[8].close == settled[7].close


async def test_settled_prefix_ignores_revisions_including_the_overlap() -> None:
    """Re-requested overlap is evidence only; it cannot revise the canonical frozen seed."""
    provider = _Provider(12)
    cache = DeployWindowCache()
    first = await _load(cache, provider)
    provider.revise(2, Decimal(222))
    provider.revise(8, Decimal(888))
    second = await _load(cache, provider, through=11)
    assert second[:9] == first[:9]
    assert second[2] == _candle(2)
    assert second[8] == _candle(8)


async def test_second_cycle_requests_only_increment_plus_overlap() -> None:
    """Extending a healthy window no longer requests the anchored lifetime each cycle."""
    provider = _Provider(12)
    cache = DeployWindowCache()
    first = await _load(cache, provider)
    before = len(provider.calls)
    second = await _load(cache, provider, through=11)
    assert second[:10] == first
    assert all(start >= _START + 8 * _MINUTE for start, _end in provider.calls[before:])
    assert second == tuple(provider.candles.values())


async def test_long_duration_restart_and_seed_correctness_at_real_adapter_bound() -> None:
    """More than 129,600 1m bars rebuild identically without sliding the EMA seed boundary."""
    count = MAX_HISTORICAL_INTERVAL_COUNT + 25
    provider = _Provider(count)
    original = await _eventually(DeployWindowCache(), provider, through=count - 1)
    assert len(original) == count
    assert all(
        (end - start) // _MINUTE <= RANGE_BLOCK_INTERVALS + 1 for start, end in provider.calls
    )
    provider.calls.clear()
    rebuilt = await _eventually(DeployWindowCache(), provider, through=count - 1)
    assert rebuilt == original == tuple(provider.candles.values())
    indicators = (
        IndicatorDefinition.model_validate(
            {"id": "ema", "kind": "ema", "input": "close", "parameters": {"period": 5}}
        ),
    )
    assert (
        calculate_indicator_rows(indicators, rebuilt)[-1]
        == calculate_indicator_rows(indicators, tuple(provider.candles.values()))[-1]
    )
    assert all(
        (end - start) // _MINUTE <= RANGE_BLOCK_INTERVALS + 1 for start, end in provider.calls
    )


async def test_real_long_duration_cold_scan_spans_cycles_without_false_missing_data() -> None:
    """A prefix longer than eight blocks has typed warming, then serves the entire seed."""
    count = MAX_HISTORICAL_INTERVAL_COUNT + RANGE_BLOCK_INTERVALS
    provider = _Provider(count)
    cache = DeployWindowCache()
    with pytest.raises(WindowCacheWarmingError) as caught:
        await _load(cache, provider, through=count - 1)
    assert caught.value.range_requests == window_cache.MAX_RANGE_REQUESTS_PER_CYCLE
    assert caught.value.product_id == "BTC-USD"
    assert caught.value.interval is CandleInterval.ONE_MINUTE
    assert caught.value.scanned_through == (
        _START + RANGE_BLOCK_INTERVALS * window_cache.MAX_RANGE_REQUESTS_PER_CYCLE * _MINUTE
    )
    assert await _eventually(cache, provider, through=count - 1) == tuple(provider.candles.values())


async def test_confirmation_and_scan_progress_survive_a_one_request_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even sparse blocks progress when confirmation consumes a separate bounded cycle."""
    monkeypatch.setattr(window_cache, "RANGE_BLOCK_INTERVALS", 4)
    monkeypatch.setattr(window_cache, "MAX_RANGE_REQUESTS_PER_CYCLE", 1)
    provider = _Provider(10, omitted=frozenset({2, 5}))
    window = await _eventually(DeployWindowCache(), provider)
    assert len(window) == 10
    assert window[2].volume == window[5].volume == 0
    assert window[2].close == window[1].close
    assert window[5].close == window[4].close
    assert all((end - start) // _MINUTE <= 5 for start, end in provider.calls)


@pytest.mark.parametrize("real_indices", [(9, 10), (0, 9, 10), ()])
async def test_empty_blocks_do_not_stall_or_fabricate_leading_or_newest_prices(
    monkeypatch: pytest.MonkeyPatch, real_indices: tuple[int, ...]
) -> None:
    """Confirmed empty starts/intermediate blocks advance; no trades anywhere stays empty."""
    monkeypatch.setattr(window_cache, "RANGE_BLOCK_INTERVALS", 3)
    monkeypatch.setattr(window_cache, "MAX_RANGE_REQUESTS_PER_CYCLE", 1)
    provider = _Provider(12, omitted=frozenset(set(range(12)) - set(real_indices)))
    window = await _eventually(DeployWindowCache(), provider, through=11)
    if not real_indices:
        assert window == ()
    else:
        assert window[0].starts_at == _START + real_indices[0] * _MINUTE
        assert window[-1].starts_at == _START + 10 * _MINUTE
        real = tuple(candle for candle in window if candle.volume > 0)
        assert tuple(candle.starts_at for candle in real) == tuple(
            _START + index * _MINUTE for index in real_indices
        )
        assert len(window) == 11 - real_indices[0]


async def test_restart_and_eviction_rebuild_same_anchored_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh and evicted caches retry with typed warming and never serve a partial seed."""
    monkeypatch.setattr(window_cache, "RANGE_BLOCK_INTERVALS", 4)
    monkeypatch.setattr(window_cache, "MAX_RANGE_REQUESTS_PER_CYCLE", 1)
    provider = _Provider(10)
    cache = DeployWindowCache(max_total_candles=5)
    original = await _eventually(cache, provider)
    assert await _eventually(DeployWindowCache(), provider) == original
    with pytest.raises(WindowCacheWarmingError):
        await _load(cache, provider, product_id="ETH-USD")
    assert {key.product_id for key in cache._windows} == {"ETH-USD"}
    with pytest.raises(WindowCacheWarmingError):
        await _load(cache, provider)
    assert await _eventually(cache, provider) == original


async def test_failure_keeps_confirmed_progress_without_serving_partial_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider error propagates; retry keeps earlier settled blocks and anchored seeds."""
    monkeypatch.setattr(window_cache, "RANGE_BLOCK_INTERVALS", 4)
    monkeypatch.setattr(window_cache, "MAX_RANGE_REQUESTS_PER_CYCLE", 1)
    provider = _Provider(10)
    cache = DeployWindowCache()
    with pytest.raises(WindowCacheWarmingError):
        await _load(cache, provider)
    provider.fail_after = len(provider.calls)
    with pytest.raises(ConnectionError, match="provider unavailable"):
        await _load(cache, provider)
    provider.fail_after = None
    assert await _eventually(cache, provider) == tuple(provider.candles.values())
    assert provider.calls[1][0] == _START + 3 * _MINUTE


async def test_as_of_cap_truncates_frozen_history_without_future_request() -> None:
    """A historical cap inside frozen coverage serves neither mutable nor future bars."""
    provider = _Provider(12)
    cache = DeployWindowCache()
    await _load(cache, provider, through=11)
    before = len(provider.calls)
    capped = await _load(cache, provider, through=3)
    assert capped == tuple(provider.candles.values())[:4]
    assert len(provider.calls) == before


async def test_as_of_inside_a_cached_no_trade_gap_withholds_a_synthetic_head() -> None:
    """A cached interior no-trade bar cannot become a fabricated newest as-of candle."""
    provider = _Provider(10, omitted=frozenset({3}))
    cache = DeployWindowCache()
    whole = await _load(cache, provider)
    assert whole[3].volume == 0
    capped = await _load(cache, provider, through=3)
    assert capped == tuple(provider.candles.values())[:3]
    # With the later real candle visible inside the cap, that interior fill is causal.
    through_real = await _load(cache, provider, through=4)
    assert through_real == whole[:5]


async def test_future_as_of_cap_cannot_leak_open_candles() -> None:
    """A caller's future cap is limited by the actual observation's closed boundary."""
    provider = _Provider(10)
    now = _START + 7 * _MINUTE + timedelta(seconds=30)
    window = await _load(DeployWindowCache(), provider, now=now)
    assert window == tuple(provider.candles.values())[:7]
    assert all(end <= _START + 7 * _MINUTE for _start, end in provider.calls)


async def test_pending_later_block_cannot_leak_through_an_earlier_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending confirmation observed beyond the cap is discarded before an earlier load."""
    monkeypatch.setattr(window_cache, "RANGE_BLOCK_INTERVALS", 4)
    monkeypatch.setattr(window_cache, "MAX_RANGE_REQUESTS_PER_CYCLE", 1)
    provider = _Provider(12, omitted=frozenset({2}))
    cache = DeployWindowCache()
    with pytest.raises(WindowCacheWarmingError):
        await _load(cache, provider, through=11)
    before = len(provider.calls)
    capped = await _eventually(cache, provider, through=1)
    assert capped == (_candle(0), _candle(1))
    assert all(end <= _START + 2 * _MINUTE for _start, end in provider.calls[before:])


async def test_concurrent_same_window_loads_do_not_duplicate_frozen_bars() -> None:
    """Serialized cache mutation keeps shared same-clock consumers deterministic."""
    provider = _Provider(10)
    cache = DeployWindowCache()
    first, second = await asyncio.gather(_load(cache, provider), _load(cache, provider))
    assert first == second == tuple(provider.candles.values())
    assert len(provider.calls) == 3


async def test_new_real_overlap_replaces_only_an_unfrozen_trailing_omission() -> None:
    """Overlap may heal an omitted bar after the last frozen trade, never replace a seed."""
    provider = _Provider(12, omitted=frozenset({8, 9}))
    cache = DeployWindowCache()
    first = await _load(cache, provider)
    assert first[-1].starts_at == _START + 7 * _MINUTE
    provider.omitted.remove(_START + 8 * _MINUTE)
    second = await _load(cache, provider)
    assert second == tuple(provider.candles.values())[:9]
    provider.omitted.clear()
    third = await _load(cache, provider, through=11)
    assert third == tuple(provider.candles.values())


def test_nonpositive_memory_budget_is_rejected() -> None:
    """A cache that could not retain any seed is a configuration error, not endless warming."""
    with pytest.raises(ValueError, match="positive"):
        DeployWindowCache(max_total_candles=0)
