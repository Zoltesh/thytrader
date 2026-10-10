"""Read-only BTC-beta evidence from settled daily candles, cached per process (ADR 0125).

Each product's β uses the 91 settled UTC daily bars ending at the newest settled daily close,
read through ``MarketDataService.get_range``: the same read-only historical path as midnight
marks, never the research dataset catalog or a deploy-anchored window. Series are cached by
``(product, daily close)``, so a process reads each product about once per UTC day. A failed
or incomplete read is retried after a short back-off. A failed read falls back to that
product's last complete series, whose age the gate then judges (``fresh_beta``). Nothing is
read unless a caller asks for products; ``load_entry_beta`` asks only when a β cap binds.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from thytrader.market_data.models import CandleInterval
from thytrader.market_data.products import is_spot_product_id
from thytrader.market_data.window_cache import PUBLICATION_SETTLE
from thytrader.risk.beta import (
    BetaEvidence,
    BetaUnavailable,
    BetaUnavailableReason,
    beta_reference,
    estimate_beta,
    reference_beta,
)
from thytrader.risk.beta_exposure import beta_cap_applies, beta_products

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import datetime

    from thytrader.market_data.models import Candle
    from thytrader.market_data.service import MarketDataService
    from thytrader.risk.beta import BetaResult
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.models import DeploymentMode, DeploymentSnapshot

BETA_DAILY_BARS = 91
"""Daily bars per read: 90 returns, one Coinbase page."""
BETA_CACHE_ENTRIES = 512
BETA_RETRY_AFTER = timedelta(minutes=15)
"""Back-off before a failed or incomplete daily read is attempted again."""
_DAILY = CandleInterval.ONE_DAY


def beta_day_close(as_of: datetime) -> datetime:
    """Return the exclusive end of the newest daily bar that has settled at ``as_of``."""
    return _DAILY.align_closed_end(as_of - PUBLICATION_SETTLE)


@dataclass(frozen=True, slots=True)
class _Series:
    """One cached daily read: candles (``None`` when the read failed) and its retry time.

    ``retry_after`` is ``None`` for a complete read, which is final for its daily close.
    """

    candles: tuple[Candle, ...] | None
    retry_after: datetime | None


class BetaHistoryCache:
    """Bounded in-process LRU of daily candle series keyed by ``(product, daily close)``.

    It also remembers each product's newest complete series so a failed read can fall back
    to it. Not shared across processes; the API and worker each read about once per day.
    """

    def __init__(self, max_entries: int = BETA_CACHE_ENTRIES) -> None:
        """Create an empty cache holding at most ``max_entries`` series and fallbacks."""
        self._max_entries = max_entries
        self._series: OrderedDict[tuple[str, datetime], _Series] = OrderedDict()
        self._complete: OrderedDict[str, tuple[Candle, ...]] = OrderedDict()

    def lookup(self, product_id: str, day_close: datetime, *, as_of: datetime) -> _Series | None:
        """Return a usable cached read, or ``None`` when one must be made now."""
        key = (product_id, day_close)
        cached = self._series.get(key)
        if cached is None:
            return None
        if cached.retry_after is not None and as_of >= cached.retry_after:
            return None
        self._series.move_to_end(key)
        return cached

    def store(
        self,
        product_id: str,
        day_close: datetime,
        *,
        candles: tuple[Candle, ...] | None,
        complete: bool,
        as_of: datetime,
    ) -> None:
        """Record one read; a complete read is final and becomes the product's fallback."""
        retry_after = None if complete else as_of + BETA_RETRY_AFTER
        self._remember(self._series, (product_id, day_close), _Series(candles, retry_after))
        if complete and candles is not None:
            self._remember(self._complete, product_id, candles)

    def clear(self) -> None:
        """Forget every cached series and fallback (tests and credential hot-swaps)."""
        self._series.clear()
        self._complete.clear()

    def fallback(self, product_id: str) -> tuple[Candle, ...] | None:
        """Return the product's newest complete series, if any read ever completed."""
        return self._complete.get(product_id)

    def _remember[K, V](self, entries: OrderedDict[K, V], key: K, value: V) -> None:
        """Insert as most recent and evict the least recently used beyond the bound."""
        entries[key] = value
        entries.move_to_end(key)
        while len(entries) > self._max_entries:
            entries.popitem(last=False)


DEFAULT_BETA_CACHE = BetaHistoryCache()
"""The process-wide cache used when a caller does not pass one."""


async def load_beta_evidence(
    market_data: MarketDataService,
    *,
    product_ids: Iterable[str],
    as_of: datetime,
    cache: BetaHistoryCache | None = None,
) -> BetaEvidence:
    """Estimate β for each product against ``BTC-<quote>`` from settled daily candles.

    The reference product is β 1 without a read. A product whose own or reference series
    cannot be read and has no complete fallback is ``FETCH_FAILED``. Staleness is judged
    by the gate against the entry's ``as_of``, not here.
    """
    history = DEFAULT_BETA_CACHE if cache is None else cache
    day_close = beta_day_close(as_of)
    results: dict[str, BetaResult] = {}
    for product_id in sorted(set(product_ids)):
        results[product_id] = await _product_beta(
            market_data, product_id, day_close=day_close, as_of=as_of, cache=history
        )
    return BetaEvidence(results=results)


async def _product_beta(
    market_data: MarketDataService,
    product_id: str,
    *,
    day_close: datetime,
    as_of: datetime,
    cache: BetaHistoryCache,
) -> BetaResult:
    """Return one product's β from its and its reference's daily series."""
    if not is_spot_product_id(product_id):
        return BetaUnavailable(
            product_id=product_id,
            reference_id="",
            reason=BetaUnavailableReason.INVALID_HISTORY,
        )
    reference_id = beta_reference(product_id)
    if product_id == reference_id:
        return reference_beta(reference_id)
    product = await _daily_series(
        market_data, product_id, day_close=day_close, as_of=as_of, cache=cache
    )
    reference = await _daily_series(
        market_data, reference_id, day_close=day_close, as_of=as_of, cache=cache
    )
    if product is None or reference is None:
        return BetaUnavailable(
            product_id=product_id,
            reference_id=reference_id,
            reason=BetaUnavailableReason.FETCH_FAILED,
        )
    return estimate_beta(product_id, product, reference)


async def _daily_series(
    market_data: MarketDataService,
    product_id: str,
    *,
    day_close: datetime,
    as_of: datetime,
    cache: BetaHistoryCache,
) -> tuple[Candle, ...] | None:
    """Return the cached or freshly read daily series, or the complete fallback on failure."""
    cached = cache.lookup(product_id, day_close, as_of=as_of)
    if cached is not None:
        return cached.candles if cached.candles is not None else cache.fallback(product_id)
    starts_at = day_close - _DAILY.duration * BETA_DAILY_BARS
    try:
        report = await market_data.get_range(product_id, _DAILY, starts_at, day_close, as_of)
    except RuntimeError, ValueError, TypeError, OSError, AttributeError, LookupError:
        # Missing history denies new beta-capped risk; it never stops protective work.
        report = None
    if report is None or report.starts_at != starts_at or report.ends_at != day_close:
        cache.store(product_id, day_close, candles=None, complete=False, as_of=as_of)
        return cache.fallback(product_id)
    candles = report.quality.candles
    cache.store(product_id, day_close, candles=candles, complete=report.complete, as_of=as_of)
    return candles


async def load_entry_beta(
    policy: RiskPolicyDefinition,
    market_data: MarketDataService | None,
    *,
    mode: DeploymentMode,
    snapshots: Sequence[DeploymentSnapshot],
    product_id: str,
    as_of: datetime,
    cache: BetaHistoryCache | None = None,
) -> BetaEvidence | None:
    """Load β evidence for one entry, and read nothing unless a β cap binds in ``mode``.

    ``snapshots`` must be the set the entry gate receives, so the products read are the
    proposed product and every same-quote product the gate sums. Returns ``None`` when no
    β cap binds (the gate ignores evidence) or when no market data is available, in which
    case the gate denies with ``BTC_BETA_UNAVAILABLE``.
    """
    if not beta_cap_applies(policy, mode) or market_data is None:
        return None
    return await load_beta_evidence(
        market_data,
        product_ids=beta_products(snapshots, mode=mode, product_id=product_id),
        as_of=as_of,
        cache=cache,
    )
