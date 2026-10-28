"""How a desk is doing against doing nothing clever (ADR 0147).

The desk's equity return since creation is set beside two buy-and-hold benchmarks over the same
window: BTC in the desk's quote currency, and an equal-weight basket of the desk's universe.
Each benchmark pays the account taker fee once to enter and once to exit, as the desk would.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from thytrader.agent_desk.market import market_rows
from thytrader.agent_desk.models import WakeOutcome
from thytrader.market_data.models import CandleInterval

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.agent_desk.market import MarketRow
    from thytrader.agent_desk.models import Desk, DeskBook, DeskWake
    from thytrader.market_data.service import MarketDataService

_PLACES = Decimal("0.000001")


class Benchmark(BaseModel):
    """One buy-and-hold return over the desk's life, after entry and exit taker fees."""

    model_config = ConfigDict(frozen=True)

    name: str
    products: tuple[str, ...]
    return_fraction: Decimal | None = None
    unavailable_reason: str | None = None


class DeskScore(BaseModel):
    """A desk's return, its activity, and the benchmarks it should beat."""

    model_config = ConfigDict(frozen=True)

    desk_id: str
    as_of: datetime
    since: datetime
    starting_cash: Decimal
    equity_quote: Decimal | None
    return_fraction: Decimal | None
    wakes: int
    # Wakes that ran the model (every wake but ``skipped`` ones, which ran no harness).
    answered_wakes: int
    wakes_by_outcome: dict[str, int]
    books_opened: int
    books_settled: int
    settled_winners: int
    btc: Benchmark
    universe: Benchmark
    beats_btc: bool | None
    beats_universe: bool | None


async def score_desk(
    desk: Desk,
    *,
    equity: Decimal | None,
    books: Sequence[DeskBook],
    wakes: Sequence[DeskWake],
    rows: Sequence[MarketRow],
    market_data: MarketDataService,
    taker_fee_rate: Decimal | None,
    now: datetime | None = None,
) -> DeskScore:
    """Score the desk at ``equity`` (None while a book cannot be marked)."""
    moment = now or datetime.now(UTC)
    marks = {row.product_id: row.last_close for row in rows}
    btc_product = f"BTC-{desk.quote_currency}"
    if btc_product not in marks:
        extra = await market_rows(market_data, (btc_product,))
        marks.update({row.product_id: row.last_close for row in extra})
    starts = {
        product: await _price_at(market_data, product, desk.created_at)
        for product in {*desk.products, btc_product}
    }
    btc = _benchmark("btc_buy_and_hold", (btc_product,), starts, marks, taker_fee_rate)
    universe = _benchmark("universe_equal_weight", desk.products, starts, marks, taker_fee_rate)
    result = None if equity is None else _q(equity / desk.starting_cash - 1)
    outcomes = {outcome.value: 0 for outcome in WakeOutcome}
    for wake in wakes:
        outcomes[wake.outcome.value] += 1
    settled = [book for book in books if book.settled_cash is not None]
    return DeskScore(
        desk_id=desk.desk_id,
        as_of=moment,
        since=desk.created_at,
        starting_cash=desk.starting_cash,
        equity_quote=equity,
        return_fraction=result,
        wakes=len(wakes),
        answered_wakes=len(wakes) - outcomes[WakeOutcome.SKIPPED.value],
        wakes_by_outcome=outcomes,
        books_opened=len(books),
        books_settled=len(settled),
        settled_winners=sum(
            1 for book in settled if (book.settled_cash or 0) > book.allocated_quote
        ),
        btc=btc,
        universe=universe,
        beats_btc=_beats(result, btc),
        beats_universe=_beats(result, universe),
    )


def _benchmark(
    name: str,
    products: Sequence[str],
    starts: dict[str, Decimal | None],
    marks: dict[str, Decimal | None],
    taker_fee_rate: Decimal | None,
) -> Benchmark:
    """Equal-weight buy-and-hold over ``products`` with a round trip of taker fees."""
    if taker_fee_rate is None:
        return Benchmark(
            name=name, products=tuple(products), unavailable_reason="account fee rates unknown"
        )
    ratios: list[Decimal] = []
    for product in products:
        start, mark = starts.get(product), marks.get(product)
        if start is None or mark is None:
            return Benchmark(
                name=name,
                products=tuple(products),
                unavailable_reason=f"no price for {product} at the desk's start or now",
            )
        ratios.append(mark / start)
    gross = sum(ratios, Decimal(0)) / len(ratios)
    net = gross * (1 - taker_fee_rate) * (1 - taker_fee_rate) - 1
    return Benchmark(name=name, products=tuple(products), return_fraction=_q(net))


def _beats(result: Decimal | None, benchmark: Benchmark) -> bool | None:
    """Whether the desk's return is above the benchmark's, when both are known."""
    if result is None or benchmark.return_fraction is None:
        return None
    return result > benchmark.return_fraction


async def _price_at(
    market_data: MarketDataService, product_id: str, moment: datetime
) -> Decimal | None:
    """The close of the last hourly candle closed at ``moment``: a price available then."""
    ends_at = CandleInterval.ONE_HOUR.align_closed_end(moment)
    starts_at = ends_at - timedelta(hours=3)
    try:
        report = await market_data.get_hourly_range(
            product_id, starts_at, ends_at, datetime.now(UTC)
        )
    except Exception:  # noqa: BLE001 - an unreadable benchmark is reported, not raised.
        return None
    candles = report.quality.candles
    return candles[-1].close if candles else None


def _q(value: Decimal) -> Decimal:
    """Fractions are reported to six places."""
    return value.quantize(_PLACES)
