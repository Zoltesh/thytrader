"""The backward and forward provider-page history walks of one ingest call.

Dispatches a claimed attempt to the newest-first backfill or the forward extension
of the published island, spends paced provider requests within the request
budget, and hands each walked segment to recording.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.market_data.models import (
    CandleInterval,
    CandleRangeReport,
    MarketDataRateLimitedError,
)
from thytrader.market_data.no_trade import fill_no_trade_gaps, no_trade_bar
from thytrader.market_data.watch_coverage import listing_horizon_start, safe_shift, utc_day_floor
from thytrader.market_data.worker_state import MarketDataMaintenanceKind, MarketDataWorkerState
from thytrader.market_data_worker.contracts import (
    IngestOutcome,
    IngestStop,
    _Direction,
    _Island,
    _logger,
    _touch_market_data_heartbeat,
    _WalkContext,
    fetch_historical_range,
)
from thytrader.market_data_worker.pages import (
    CandlePage,
    merge_confirmed_pages,
    run_end,
    split_page,
)
from thytrader.market_data_worker.recording import (
    _log_chunk_incomplete,
    _log_provider_unavailable,
    _publish_and_record,
    _record_dataset_unverifiable,
    _record_without_publication,
)
from thytrader.market_data_worker.targets import _record_failure

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    from thytrader.market_data.models import Candle


# Requests one walk may spend beyond its budget while it searches below the segment for
# the next provider candle. Such a search publishes nothing until it finds one, so a
# budget stop would repeat it every cycle. Its cost is bounded: pages to the UTC day
# boundary, then confirmed daily-granularity probes back to the listing horizon (at most
# eleven 350-day pages for a ten-year ceiling).
LISTING_SEARCH_REQUEST_ALLOWANCE = 48


def _island_from(prior: MarketDataWorkerState | None) -> _Island | None:
    """Return the prior complete island, or None when state lacks full coverage evidence."""
    if (
        prior is None
        or not prior.complete
        or prior.content_fingerprint is None
        or prior.covered_starts_at is None
        or prior.covered_ends_at is None
    ):
        return None
    return _Island(
        state=prior,
        fingerprint=prior.content_fingerprint,
        starts_at=prior.covered_starts_at,
        ends_at=prior.covered_ends_at,
    )


async def _walk(
    context: _WalkContext,
    *,
    maintenance_kind: MarketDataMaintenanceKind,
    lookback_start: datetime,
    prior: MarketDataWorkerState | None,
    verify_island: bool,
) -> IngestOutcome:
    """Dispatch the claimed attempt to the newest-first or forward page walk."""
    if maintenance_kind is MarketDataMaintenanceKind.INITIAL_BACKFILL:
        backward = _BackwardWalk(context, lookback_start, None)
        return await _finish_backward(context, backward, None, await backward.run())
    island = _island_from(prior)
    if island is None:
        await _record_failure(
            context.state_store,
            context.attempt,
            code="incomplete_range",
            message="Historical market-data range was incomplete or inconsistent.",
            next_retry_at=context.retry_at,
        )
        return context.outcome(IngestStop.FAILED)
    if verify_island and not await _island_verifies(context, island):
        return context.outcome(IngestStop.FAILED)
    if maintenance_kind is MarketDataMaintenanceKind.PREFIX_BACKFILL:
        backward = _BackwardWalk(context, lookback_start, island)
        return await _finish_backward(context, backward, island, await backward.run())
    if island.ends_at >= context.closed_end:
        return await _record_without_publication(context, island, None, IngestStop.CURRENT)
    forward = _ForwardWalk(context, island)
    return await _finish_forward(context, forward, island, await forward.run())


async def _island_verifies(context: _WalkContext, island: _Island) -> bool:
    """Deep-verify the island once per process before extending it; record failure if not."""
    try:
        context.dataset_store.load_manifest(island.fingerprint)
    except Exception:  # noqa: BLE001 - an unreadable island must fail closed before fetching.
        await _record_dataset_unverifiable(context)
        return False
    return True


async def _island_edge(context: _WalkContext, island: _Island, *, newest: bool) -> Candle | None:
    """Load the island's stored first or last bar; record a failure when it is unreadable.

    A walk needs it when the provider omits the overlap bar because that bar is itself a
    no-trade bar: the extension then starts from the stored bar instead of inventing one.
    """
    try:
        return context.dataset_store.load_edge_candle(island.fingerprint, newest=newest)
    except Exception:  # noqa: BLE001 - an unreadable island must fail closed before publishing.
        await _record_dataset_unverifiable(context)
        return None


async def _request(
    context: _WalkContext,
    starts_at: datetime,
    ends_at: datetime,
    timeframe: CandleInterval,
) -> CandleRangeReport | IngestStop:
    """Spend one paced provider request; map throttles and failures to a walk stop."""
    if not await context.pacer.acquire():
        return IngestStop.STOPPED
    context.budget.spent += 1
    await _touch_market_data_heartbeat(context.heartbeat_store, context.now_factory)
    try:
        report = await fetch_historical_range(
            context.service,
            context.product_id,
            timeframe,
            starts_at,
            ends_at,
            context.closed_end,
        )
    except MarketDataRateLimitedError:
        cooldown = context.pacer.throttled_by_provider()
        _logger.warning(
            "market_data_ingestion_rate_limited product_id=%s timeframe=%s cooldown_seconds=%.1f",
            context.product_id,
            timeframe.value,
            cooldown,
        )
        return IngestStop.RATE_LIMITED
    except Exception as error:  # noqa: BLE001 - provider boundary is intentionally fail-closed.
        context.pacer.completed()
        _log_provider_unavailable(context.product_id, timeframe, starts_at, ends_at, error)
        return IngestStop.FAILED
    context.pacer.completed()
    return report


async def _fetch_page(
    context: _WalkContext,
    starts_at: datetime,
    ends_at: datetime,
    direction: _Direction,
    *,
    timeframe: CandleInterval | None = None,
    cutoff: datetime | None = None,
) -> CandlePage | IngestStop:
    """Fetch one page, re-fetching once before acting on a settled or inconsistent gap.

    A bar counts as missing only when both responses omit it, so a transient short page
    never becomes a no-trade bar or a listing floor. A missing bar at or after ``cutoff``
    (default: the walk's settle window) is not confirmed; the walk waits for it.
    ``timeframe`` lets the listing search request daily candles.
    """
    interval = context.timeframe if timeframe is None else timeframe
    settle = context.cutoff if cutoff is None else cutoff
    report = await _request(context, starts_at, ends_at, interval)
    if isinstance(report, IngestStop):
        return report
    page = split_page(report, starts_at, ends_at, interval)
    if not page.needs_confirmation(settle):
        return page
    confirmed = await _request(context, starts_at, ends_at, interval)
    if isinstance(confirmed, IngestStop):
        return confirmed
    merged = merge_confirmed_pages(page, split_page(confirmed, starts_at, ends_at, interval))
    if not merged.complete:
        _log_chunk_incomplete(
            context.product_id, interval, starts_at, ends_at, confirmed, direction
        )
    return merged


class _BackwardWalk:
    """Newest-first page walk that assembles one gap-filled segment toward the lookback start.

    Confirmed no-trade intervals between real candles (and, for initial backfill, after
    the newest real candle up to the first unsettled bar) become flat bars (ADR 0095).
    The walk is anchored when a real candle at or before the lookback start begins the
    segment; when the lookback start itself had no trades, the newest candle below it
    prices flat bars from the lookback start instead. A confirmed page with no candle at
    all starts a listing search: pages to the UTC day boundary, then daily-candle probes
    that skip whole days without trades. Only a search that reaches the listing horizon
    without any candle records ``history_floor_at``: the market had not traded yet.
    Prefix backfill starts one overlap bar past the island start; when the provider omits
    that bar (a stored no-trade bar), the segment ends at the stored island head instead.
    """

    def __init__(
        self, context: _WalkContext, lookback_start: datetime, island: _Island | None
    ) -> None:
        """Start at the newest closed bar, or one overlap bar past the island start."""
        self._context = context
        self._target = lookback_start
        self._horizon = listing_horizon_start(context.closed_end, context.timeframe)
        self._island = island
        self._pages: list[tuple[Candle, ...]] = []
        self._cursor = (
            context.closed_end
            if island is None
            else safe_shift(
                island.starts_at,
                context.bar,
                "Market-data worker cannot represent a prefix overlap end.",
            )
        )
        self._segment_end: datetime | None = None if island is None else self._cursor
        self._anchor_close: Decimal | None = None
        self._searching = False
        self._probed_at: datetime | None = None
        self.floor: datetime | None = None
        self.needs_head = False

    async def run(self) -> IngestStop:
        """Fetch pages until the segment is anchored, the listing floor, the budget, or a stop."""
        while not self._anchored():
            if self._cursor <= self._horizon:
                return self._listing_floor()
            if self._cursor <= self._target and self._oldest_start() is None:
                # No trade anywhere in the lookback: nothing to anchor or publish.
                return IngestStop.COMPLETE
            if self._out_of_budget():
                return IngestStop.BUDGET
            if self._should_probe_days():
                stop = await self._skip_days_without_trades()
            else:
                stop = await self._fetch_and_absorb()
            if stop is not None:
                return stop
        return IngestStop.COMPLETE

    def segment(self, head: Candle | None) -> tuple[Candle, ...]:
        """Return the gap-filled segment, oldest first; empty when nothing real was found."""
        real = [candle for page in reversed(self._pages) for candle in page]
        if head is not None:
            real.append(head)
        filled = fill_no_trade_gaps(real, self._context.timeframe, through=self._segment_end)
        if not filled or self._anchor_close is None or filled[0].starts_at <= self._target:
            return filled
        leading: list[Candle] = []
        cursor = self._target
        while cursor < filled[0].starts_at:
            leading.append(no_trade_bar(cursor, self._anchor_close))
            cursor = cursor + self._context.bar
        return (*leading, *filled)

    def _oldest_start(self) -> datetime | None:
        """Return the oldest real bar the segment or its island covers."""
        if self._pages:
            return self._pages[-1][0].starts_at
        if self._island is not None:
            return self._island.starts_at
        return None

    def _anchored(self) -> bool:
        """True once the segment reaches back to the lookback start through real prices."""
        if self._anchor_close is not None:
            return True
        oldest = self._oldest_start()
        return oldest is not None and oldest <= self._target

    def _listing_floor(self) -> IngestStop:
        """End a listing search that found no provider candle before the segment."""
        self.floor = self._oldest_start()
        return IngestStop.LISTING_FLOOR

    def _out_of_budget(self) -> bool:
        """True when the walk must stop; a listing search may use a bounded allowance."""
        if self._searching:
            return self._context.budget.exhausted(allowance=LISTING_SEARCH_REQUEST_ALLOWANCE)
        return self._context.budget.exhausted()

    def _should_probe_days(self) -> bool:
        """True when a listing search sits on a UTC day boundary it has not probed yet."""
        return (
            self._searching
            and self._context.can_probe_days
            and self._cursor == utc_day_floor(self._cursor)
            and self._probed_at != self._cursor
        )

    def _page_start(self) -> datetime:
        """Return the next page start: clipped at the lookback start, then the horizon.

        A listing search does not page across a UTC day boundary, so it reaches one and
        can probe the whole days below it with daily candles.
        """
        bound = self._target if self._cursor > self._target else self._horizon
        earliest = safe_shift(
            self._cursor,
            -self._context.page_span,
            "Market-data worker cannot represent a page start.",
        )
        start = max(bound, earliest)
        if self._searching and self._context.can_probe_days:
            start = max(start, utc_day_floor(self._cursor - self._context.bar))
        return start

    async def _fetch_and_absorb(self) -> IngestStop | None:
        """Fetch the next older page and fold its confirmed candles into the segment."""
        direction: _Direction = "initial" if self._island is None else "prefix"
        page = await _fetch_page(self._context, self._page_start(), self._cursor, direction)
        if isinstance(page, IngestStop):
            return page
        return self._absorb(page)

    def _absorb(self, page: CandlePage) -> IngestStop | None:
        """Keep the page's usable candles; return a stop when the segment cannot grow."""
        if not page.consistent:
            return IngestStop.INCONSISTENT
        unsettled = page.first_unsettled_missing(self._context.cutoff)
        if self._segment_end is None:
            # Initial backfill: the segment ends before the first bar still settling.
            self._segment_end = page.ends_at if unsettled is None else unsettled
        elif unsettled is not None:
            return IngestStop.UNSETTLED
        usable = tuple(candle for candle in page.candles() if candle.starts_at < self._segment_end)
        if self._island is not None and page.ends_at == self._segment_end:
            self.needs_head = not usable or usable[-1].starts_at != self._island.starts_at
        previous_oldest = self._oldest_start()
        # A page that adds no candle older than the segment (only the overlap bar, or
        # nothing) leaves a confirmed-empty span below it: the listing search continues.
        self._searching = not usable or (
            previous_oldest is not None and usable[0].starts_at >= previous_oldest
        )
        self._cursor = page.starts_at
        if not usable:
            return None
        if page.ends_at <= self._target:
            # Below the lookback start: the newest trade prices the bars from that start.
            self._anchor_close = usable[-1].close
            return None
        self._pages.append(usable)
        return None

    async def _skip_days_without_trades(self) -> IngestStop | None:
        """Move the search cursor past whole UTC days that have no daily candle.

        Probes newest first with confirmed daily-granularity pages. The cursor lands at the
        end of the newest day that traded, or at the horizon when no day before it did.
        """
        self._probed_at = self._cursor
        lower = utc_day_floor(self._horizon)
        span = CandleInterval.ONE_DAY.duration * self._context.page_candles
        day_end = self._cursor
        while day_end > lower:
            start = max(lower, day_end - span)
            page = await _fetch_page(
                self._context,
                start,
                day_end,
                "listing_probe",
                timeframe=CandleInterval.ONE_DAY,
                cutoff=day_end,
            )
            if isinstance(page, IngestStop):
                return page
            if not page.consistent:
                return IngestStop.INCONSISTENT
            traded = page.candles()
            if traded:
                self._cursor = traded[-1].starts_at + CandleInterval.ONE_DAY.duration
                self._probed_at = self._cursor
                return None
            day_end = start
        self._cursor = self._horizon
        return None


class _ForwardWalk:
    """Oldest-first page walk from the island's overlap bar toward the newest closed bar.

    Every page extends the island: the walk never starts a detached island and never
    records or moves ``history_floor_at``. Confirmed no-trade intervals become flat bars at
    the previous close (ADR 0095). A missing bar inside the settle window ends the walk
    until a later cycle, so nothing after it is published yet and a late candle is never
    replaced. When the provider omits the overlap bar (a stored no-trade bar), the
    extension starts from the stored island tail.
    """

    def __init__(self, context: _WalkContext, island: _Island) -> None:
        """Start at the island's last bar so the first page overlaps it."""
        self._context = context
        self._island = island
        self._overlap = safe_shift(
            island.ends_at,
            -context.timeframe.duration,
            "Market-data worker cannot represent its incremental range start.",
        )
        self._cursor = self._overlap
        self._real: list[Candle] = []
        self._end = island.ends_at

    async def run(self) -> IngestStop:
        """Fetch pages until the newest closed bar, an unsettled bar, the budget, or a stop."""
        while self._cursor < self._context.closed_end:
            if self._context.budget.exhausted():
                return IngestStop.BUDGET
            latest = safe_shift(
                self._cursor,
                self._context.page_span,
                "Market-data worker cannot represent a page end.",
            )
            page_end = min(self._context.closed_end, latest)
            page = await _fetch_page(self._context, self._cursor, page_end, "forward")
            if isinstance(page, IngestStop):
                return page
            if not page.consistent:
                return IngestStop.INCONSISTENT
            unsettled = page.first_unsettled_missing(self._context.cutoff)
            boundary = page.ends_at if unsettled is None else unsettled
            self._real.extend(candle for candle in page.candles() if candle.starts_at < boundary)
            self._end = max(self._end, boundary)
            if unsettled is not None:
                return IngestStop.UNSETTLED
            self._cursor = page_end
        return IngestStop.COMPLETE

    @property
    def needs_tail(self) -> bool:
        """True when the extension must start from the stored island tail bar."""
        return self._end > self._island.ends_at and (
            not self._real or self._real[0].starts_at != self._overlap
        )

    def extension(self, tail: Candle | None) -> tuple[Candle, ...]:
        """Return the overlap bar onward, gap-filled through the publishable end."""
        if self._end <= self._island.ends_at:
            return ()
        real = list(self._real)
        if tail is not None and (not real or real[0].starts_at != self._overlap):
            real.insert(0, tail)
        return fill_no_trade_gaps(real, self._context.timeframe, through=self._end)


async def _finish_backward(
    context: _WalkContext,
    walk: _BackwardWalk,
    island: _Island | None,
    stop: IngestStop,
) -> IngestOutcome:
    """Publish the walked segment as a new island or a prefix revision, then record state."""
    head: Candle | None = None
    if walk.needs_head and island is not None:
        head = await _island_edge(context, island, newest=False)
        if head is None:
            return context.outcome(IngestStop.FAILED)
    candles = walk.segment(head)
    if candles and (island is None or candles[0].starts_at < island.starts_at):
        return await _publish_and_record(
            context,
            candles,
            extend_fingerprint=None if island is None else island.fingerprint,
            history_floor_at=walk.floor,
            stop=stop,
        )
    return await _record_without_publication(context, island, walk.floor, stop)


async def _finish_forward(
    context: _WalkContext,
    walk: _ForwardWalk,
    island: _Island,
    stop: IngestStop,
) -> IngestOutcome:
    """Publish the island's forward extension, then record state; floors never move here."""
    tail: Candle | None = None
    if walk.needs_tail:
        tail = await _island_edge(context, island, newest=True)
        if tail is None:
            return context.outcome(IngestStop.FAILED)
    extension = walk.extension(tail)
    if extension and run_end(extension, context.timeframe) > island.ends_at:
        return await _publish_and_record(
            context,
            extension,
            extend_fingerprint=island.fingerprint,
            history_floor_at=None,
            stop=stop,
        )
    return await _record_without_publication(context, island, None, stop)
