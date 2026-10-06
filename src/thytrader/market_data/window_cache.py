"""Fixed-seed execution history with bounded requests and a mutable tail (ADR 0113).

The deploy warmup start never slides. Settled provider history is cached in process memory;
restart/eviction rebuilds that same prefix, withholding evaluation until the bounded scan
finishes. Each range is at most one Coinbase candle page, but this is not a global HTTP
budget or a bound on retained memory/full-history indicator computation. No operational
state or synthetic newest bar is stored.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from thytrader.market_data.models import HISTORICAL_REQUEST_MAX_CANDLES, CandleInterval
from thytrader.market_data.no_trade import (
    fill_no_trade_gaps,
    is_no_trade_bar,
    merge_confirmed_candles,
)
from thytrader.market_data.window_state import WindowCacheWarmingError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.models import Candle, CandleRangeReport

# Leave room for one overlap while keeping even cold fetches to one Coinbase page.
RANGE_BLOCK_INTERVALS = HISTORICAL_REQUEST_MAX_CANDLES - 1
MAX_RANGE_REQUESTS_PER_CYCLE = 8
# LRU ceiling across windows; one larger active window is retained to avoid rebuild loops.
MAX_TOTAL_CACHED_CANDLES = 2_000_000
# Match ADR 0104's publication deadline; the newest requested bar always remains mutable.
PUBLICATION_SETTLE = timedelta(seconds=120)


class RangeFetcher(Protocol):
    """Fetch a validated half-open range, as ``MarketDataService.get_range`` does."""

    async def __call__(self, starts_at: datetime, ends_at: datetime) -> CandleRangeReport:
        """Return candles for ``[starts_at, ends_at)``."""
        ...


@dataclass(frozen=True, slots=True)
class _WindowKey:
    """Product, clock and immutable deploy warmup boundary within one provider generation."""

    product_id: str
    interval: CandleInterval
    starts_at: datetime


@dataclass(frozen=True, slots=True)
class _PendingBlock:
    """First observation of a sparse settled block awaiting confirmation on another call."""

    starts_at: datetime
    ends_at: datetime
    candles: tuple[Candle, ...]


@dataclass(slots=True)
class _WindowState:
    """Canonical settled candles and scan progress, including confirmed empty blocks.

    ``scanned_end`` advances even without trades. Missing intervals after the last real
    candle stay absent until later real evidence arrives; they cannot supply a price.
    An incomplete block awaiting confirmation is retained so even a one-call budget
    makes progress, instead of repeatedly paying for its first fetch.
    """

    scanned_end: datetime
    frozen: list[Candle] = field(default_factory=list)
    pending: _PendingBlock | None = None


@dataclass(slots=True)
class _Budget:
    """Ceiling on top-level range calls including overlap and confirmation requests."""

    used: int = 0

    def allow(self) -> bool:
        """True when another range call can be issued."""
        return self.used < MAX_RANGE_REQUESTS_PER_CYCLE


class DeployWindowCache:
    """Provider-generation-local anchored prefix cache with serialized mutation.

    A new service (restart or credential replacement) starts empty. Cold rebuilding is
    transient warming, while an exhausted *completed* scan can honestly return short or
    empty coverage. Late revisions in the 120-second tail are read on every call; revisions
    to already frozen history are ignored until a new cache generation rebuilds it.
    """

    def __init__(self, *, max_total_candles: int = MAX_TOTAL_CACHED_CANDLES) -> None:
        """Initialize an LRU candle budget; a single larger window is deliberately exempt."""
        if max_total_candles < 1:
            raise ValueError("Window cache candle budget must be positive.")
        self._max_total_candles = max_total_candles
        self._windows: dict[_WindowKey, _WindowState] = {}
        self._lock = asyncio.Lock()

    async def closed_window(
        self,
        fetch_range: RangeFetcher,
        *,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        last_closed_start: datetime,
        now: datetime,
    ) -> tuple[Candle, ...]:
        """Return the exact anchored series, or raise ``WindowCacheWarmingError`` to retry.

        Only bars closed at ``now`` and at/before the as-of cap can be returned or fetched.
        Freeze bars only after close + 120 seconds, and never the newest requested bar.
        A one-bar overlap is refetched but cannot revise an immutable prefix. The entire
        mutable tail is refetched, so cached values cannot hide a newly missing head.
        Provider exceptions propagate while retaining already confirmed scan progress.
        """
        if starts_at > last_closed_start:
            return ()
        requested_end = min(last_closed_start + interval.duration, interval.align_closed_end(now))
        if requested_end <= starts_at:
            return ()
        key = _WindowKey(product_id, interval, starts_at)
        async with self._lock:
            state = self._windows.get(key, _WindowState(scanned_end=starts_at))
            self._remember(key, state)
            budget = _Budget()
            settle_end = min(
                requested_end - interval.duration,
                interval.align_closed_end(now - PUBLICATION_SETTLE),
            )
            try:
                await self._extend_prefix(
                    state, fetch_range, key, settle_end, requested_end, budget
                )
                if requested_end <= state.scanned_end:
                    return _truncate(state.frozen, requested_end)
                if not budget.allow():
                    raise self._warming(key, state, requested_end, budget)
                # The overlap is provider evidence only; frozen candles always win there.
                fetch_start = max(starts_at, state.scanned_end - interval.duration)
                tail = await self._fetch(fetch_range, fetch_start, requested_end, budget)
                fresh = _after_frozen(state, tail)
                return _with_tail(state, fresh, interval)
            finally:
                self._remember(key, state)

    async def _extend_prefix(
        self,
        state: _WindowState,
        fetch_range: RangeFetcher,
        key: _WindowKey,
        settle_end: datetime,
        requested_end: datetime,
        budget: _Budget,
    ) -> None:
        """Scan settled blocks forward, persisting confirmation and empty-block progress."""
        while state.scanned_end < settle_end:
            if not budget.allow():
                raise self._warming(key, state, requested_end, budget)
            block = state.pending
            if block is not None and block.ends_at > settle_end:
                # An earlier as-of request cannot depend on later evidence in a pending block.
                state.pending = None
                block = None
            if block is None:
                end = min(
                    state.scanned_end + key.interval.duration * RANGE_BLOCK_INTERVALS, settle_end
                )
                start = max(key.starts_at, state.scanned_end - key.interval.duration)
                fetched = await self._fetch(fetch_range, start, end, budget)
                block = _PendingBlock(start, end, fetched)
                if len(fetched) != (end - start) // key.interval.duration:
                    state.pending = block
                    continue
            elif state.pending is not None:
                confirmation = await self._fetch(
                    fetch_range, block.starts_at, block.ends_at, budget
                )
                block = _PendingBlock(
                    block.starts_at,
                    block.ends_at,
                    merge_confirmed_candles(block.candles, confirmation),
                )
            self._freeze_block(state, block, key.interval)

    async def _fetch(
        self,
        fetch_range: RangeFetcher,
        start: datetime,
        end: datetime,
        budget: _Budget,
    ) -> tuple[Candle, ...]:
        """Count one request and reject out-of-range provider evidence at this boundary."""
        budget.used += 1
        report = await fetch_range(start, end)
        return tuple(candle for candle in report.quality.candles if start <= candle.starts_at < end)

    def _freeze_block(
        self, state: _WindowState, block: _PendingBlock, interval: CandleInterval
    ) -> None:
        """Append only settled evidence and confirmed interior no-trade bars, never a head."""
        fresh = _after_frozen(state, block.candles)
        context = (state.frozen[-1],) if state.frozen else ()
        if fresh:
            state.frozen.extend(fill_no_trade_gaps((*context, *fresh), interval)[len(context) :])
        state.scanned_end = block.ends_at
        state.pending = None

    def _warming(
        self, key: _WindowKey, state: _WindowState, requested_end: datetime, budget: _Budget
    ) -> WindowCacheWarmingError:
        """Distinguish unfinished local work from completed but genuinely short coverage."""
        return WindowCacheWarmingError(
            product_id=key.product_id,
            interval=key.interval,
            starts_at=key.starts_at,
            scanned_through=state.scanned_end,
            requested_end=requested_end,
            range_requests=budget.used,
        )

    def _remember(self, key: _WindowKey, state: _WindowState) -> None:
        """Touch this window and evict whole idle windows, not indicator seed prefixes."""
        self._windows.pop(key, None)
        self._windows[key] = state
        total = sum(_retained_count(window) for window in self._windows.values())
        while total > self._max_total_candles and len(self._windows) > 1:
            victim = next(iter(self._windows))
            total -= _retained_count(self._windows.pop(victim))


def _after_frozen(state: _WindowState, candles: Sequence[Candle]) -> tuple[Candle, ...]:
    """Keep new real evidence, including an overlap omitted before but not yet frozen."""
    return tuple(
        candle
        for candle in candles
        if not state.frozen or candle.starts_at > state.frozen[-1].starts_at
    )


def _retained_count(state: _WindowState) -> int:
    """Count stored evidence, including a block awaiting its confirmation request."""
    return len(state.frozen) + (len(state.pending.candles) if state.pending is not None else 0)


def _truncate(frozen: Sequence[Candle], requested_end: datetime) -> tuple[Candle, ...]:
    """Cap history and withhold a synthetic head whose following evidence is after the cap."""
    selected = tuple(candle for candle in frozen if candle.starts_at < requested_end)
    end = len(selected)
    while end and is_no_trade_bar(selected[end - 1]):
        end -= 1
    return selected[:end]


def _with_tail(
    state: _WindowState, fresh: tuple[Candle, ...], interval: CandleInterval
) -> tuple[Candle, ...]:
    """Join canonical history to fresh evidence without filling mutable or newest holes."""
    context = (state.frozen[-1],) if state.frozen else ()
    # Scanned empty history has been confirmed but needs a later real candle before it
    # can be honestly filled. Nothing before the first real trade is ever invented.
    bridge = (
        fill_no_trade_gaps(context, interval, through=min(state.scanned_end, fresh[0].starts_at))[
            len(context) :
        ]
        if context and fresh
        else ()
    )
    return (*state.frozen, *bridge, *fresh)
