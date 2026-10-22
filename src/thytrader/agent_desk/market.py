"""Compact per-product market summaries for a desk briefing (ADR 0147).

One row per product from closed 1h candles: last close, returns over 1h/4h/24h/7d, ATR(14) as a
fraction of price, and the last 24h volume against its 7-day daily average. Missing or short
history leaves a field ``None``; nothing is interpolated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from thytrader.market_data.models import CandleInterval, parse_candle_interval
from thytrader.trading.models import resolved_product_id, snapshot_positions

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.models import Candle
    from thytrader.market_data.service import MarketDataService
    from thytrader.trading.models import DeploymentSnapshot

_HOURS = 24 * 7 + 16
_STALE_HOURS = 2
_ATR_PERIOD = 14


class MarketRow(BaseModel):
    """One product's recent behaviour, in its quote currency."""

    model_config = ConfigDict(frozen=True)

    product_id: str
    last_close: Decimal | None = None
    last_bar_starts_at: datetime | None = None
    return_1h: Decimal | None = None
    return_4h: Decimal | None = None
    return_24h: Decimal | None = None
    return_7d: Decimal | None = None
    atr_fraction: Decimal | None = None
    volume_24h_vs_7d_avg: Decimal | None = None
    unavailable_reason: str | None = None


def summarize(
    product_id: str, candles: Sequence[Candle], *, newest_expected: datetime | None = None
) -> MarketRow:
    """Summarize closed 1h candles, newest last.

    With ``newest_expected`` (the start of the latest closed hour), a newest bar more than
    ``_STALE_HOURS`` older than it leaves the row without a price: stale evidence is unknown.
    """
    if not candles:
        return MarketRow(product_id=product_id, unavailable_reason="no closed 1h candles")
    last = candles[-1]
    if newest_expected is not None and last.starts_at < newest_expected - timedelta(
        hours=_STALE_HOURS
    ):
        return MarketRow(
            product_id=product_id,
            last_bar_starts_at=last.starts_at,
            unavailable_reason=(
                f"stale: the newest closed 1h bar starts at {last.starts_at:%Y-%m-%dT%H:%MZ}"
            ),
        )
    closes = {candle.starts_at: candle.close for candle in candles}
    return MarketRow(
        product_id=product_id,
        last_close=last.close,
        last_bar_starts_at=last.starts_at,
        return_1h=_return(closes, last, 1),
        return_4h=_return(closes, last, 4),
        return_24h=_return(closes, last, 24),
        return_7d=_return(closes, last, 24 * 7),
        atr_fraction=_atr_fraction(candles),
        volume_24h_vs_7d_avg=_relative_volume(candles),
    )


async def market_rows(
    market_data: MarketDataService, products: Sequence[str], *, now: datetime | None = None
) -> tuple[MarketRow, ...]:
    """Fetch and summarize each product; a failed read is a row with its reason."""
    moment = now or datetime.now(UTC)
    ends_at = CandleInterval.ONE_HOUR.align_closed_end(moment)
    starts_at = ends_at - timedelta(hours=_HOURS)
    rows: list[MarketRow] = []
    for product_id in products:
        try:
            report = await market_data.get_hourly_range(product_id, starts_at, ends_at, moment)
        except Exception as error:  # noqa: BLE001 - one product's read must not end the briefing.
            rows.append(
                MarketRow(
                    product_id=product_id,
                    unavailable_reason=f"market data read failed ({type(error).__name__})",
                )
            )
            continue
        newest = ends_at - timedelta(hours=1)
        rows.append(summarize(product_id, report.quality.candles, newest_expected=newest))
    return tuple(rows)


def _return(closes: dict[datetime, Decimal], last: Candle, hours: int) -> Decimal | None:
    """Close-to-close return over ``hours``, or None when the bar that long ago is missing."""
    then = closes.get(last.starts_at - timedelta(hours=hours))
    if then is None or then <= 0:
        return None
    return _round(last.close / then - 1)


def _atr_fraction(candles: Sequence[Candle]) -> Decimal | None:
    """Simple-average true range over the last 14 bars, as a fraction of the last close."""
    if len(candles) <= _ATR_PERIOD or candles[-1].close <= 0:
        return None
    ranges = []
    for previous, current in zip(
        candles[-_ATR_PERIOD - 1 : -1], candles[-_ATR_PERIOD:], strict=True
    ):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return _round(sum(ranges, start=Decimal(0)) / _ATR_PERIOD / candles[-1].close)


def _relative_volume(candles: Sequence[Candle]) -> Decimal | None:
    """The last 24 bars' volume against the 7-day daily average."""
    if len(candles) < 24 * 7:
        return None
    week = sum((candle.volume for candle in candles[-24 * 7 :]), start=Decimal(0))
    if week <= 0:
        return None
    day = sum((candle.volume for candle in candles[-24:]), start=Decimal(0))
    return _round(day / (week / 7))


def _round(value: Decimal) -> Decimal:
    """Six significant decimals keep the briefing small and stable."""
    return value.quantize(Decimal("0.000001"))


async def fresh_marks(
    market_data: MarketDataService,
    snapshots: Sequence[DeploymentSnapshot],
    *,
    now: datetime | None = None,
) -> dict[str, tuple[Decimal, datetime]]:
    """Each held product's latest close on its book's clock, with when it closed, if current.

    A newest candle that started more than three of its intervals ago is not a mark. Callers keep
    whichever close is newer between this and the hourly row.
    """
    moment = now or datetime.now(UTC)
    marks: dict[str, tuple[Decimal, datetime]] = {}
    for snapshot in snapshots:
        interval = parse_candle_interval(snapshot.deployment.timeframe or "1h")
        for position in snapshot_positions(snapshot):
            product_id = resolved_product_id(position.product_id, snapshot.deployment)
            if product_id in marks:
                continue
            mark = await _current_close(market_data, product_id, interval, moment)
            if mark is not None:
                marks[product_id] = mark
    return marks


def newest_marks(
    rows: Sequence[MarketRow], fresh: dict[str, tuple[Decimal, datetime]]
) -> dict[str, Decimal]:
    """Per product, the close whose bar ended last: the hourly row or the book's own clock."""
    closes: dict[str, tuple[Decimal, datetime]] = {}
    for row in rows:
        if row.last_close is not None and row.last_bar_starts_at is not None:
            closes[row.product_id] = (row.last_close, row.last_bar_starts_at + timedelta(hours=1))
    for product_id, (price, closed_at) in fresh.items():
        current = closes.get(product_id)
        if current is None or closed_at > current[1]:
            closes[product_id] = (price, closed_at)
    return {product_id: price for product_id, (price, _) in closes.items()}


async def _current_close(
    market_data: MarketDataService, product_id: str, interval: CandleInterval, moment: datetime
) -> tuple[Decimal, datetime] | None:
    """The newest close and its end if that candle started within three intervals of ``moment``."""
    try:
        preview = await market_data.get_preview(product_id, interval)
    except Exception:  # noqa: BLE001 - an unreadable mark stays unknown.
        return None
    candles = preview.quality.candles
    if candles and candles[-1].starts_at + 3 * interval.duration >= moment:
        return candles[-1].close, candles[-1].starts_at + interval.duration
    return None
