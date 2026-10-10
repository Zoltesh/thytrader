"""BTC-beta evidence loader and per-process cache (ADR 0125)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.beta_support import DailyBetaProvider
from thytrader.market_data.models import CandleInterval
from thytrader.risk.accounting_evidence import bound_risk_market_data, risk_market_data_scope
from thytrader.risk.beta import (
    BetaEstimate,
    BetaEvidence,
    BetaUnavailable,
    BetaUnavailableReason,
    fresh_beta,
)
from thytrader.risk.beta_evidence import (
    BETA_DAILY_BARS,
    BETA_RETRY_AFTER,
    BetaHistoryCache,
    beta_day_close,
    load_beta_evidence,
)

_AS_OF = datetime(2026, 10, 9, 12, tzinfo=UTC)
_CLOSE = datetime(2026, 10, 9, tzinfo=UTC)


def _estimate(evidence: BetaEvidence, product_id: str) -> BetaEstimate:
    """Return the product's estimate, failing the test when it is unavailable."""
    result = evidence.result_for(product_id)
    assert isinstance(result, BetaEstimate), result
    return result


def _unavailable(evidence: BetaEvidence, product_id: str) -> BetaUnavailable:
    """Return the product's unavailable result, failing the test when it has a β."""
    result = evidence.result_for(product_id)
    assert isinstance(result, BetaUnavailable), result
    return result


def test_day_close_waits_for_the_daily_bar_to_settle() -> None:
    """The newest settled daily close moves at 00:02 UTC, not at midnight."""
    assert beta_day_close(_AS_OF) == _CLOSE
    assert beta_day_close(_CLOSE + timedelta(seconds=119)) == _CLOSE - timedelta(days=1)
    assert beta_day_close(_CLOSE + timedelta(seconds=120)) == _CLOSE


@pytest.mark.anyio
async def test_loader_reads_ninety_one_settled_daily_bars_per_product() -> None:
    """One explicit 1d range per product and one for the shared reference."""
    provider = DailyBetaProvider(betas={"SOL-USDC": 1.5, "ETH-USDC": 1.2})

    evidence = await load_beta_evidence(
        provider.service(),
        product_ids=("SOL-USDC", "ETH-USDC", "SOL-USDC"),
        as_of=_AS_OF,
        cache=BetaHistoryCache(),
    )

    assert _estimate(evidence, "SOL-USDC").beta == Decimal("1.50")
    assert _estimate(evidence, "ETH-USDC").beta == Decimal("1.20")
    assert _estimate(evidence, "SOL-USDC").returns == 90
    assert _estimate(evidence, "SOL-USDC").last_close == _CLOSE
    assert sorted(provider.requested_products()) == ["BTC-USDC", "ETH-USDC", "SOL-USDC"]
    start = _CLOSE - timedelta(days=BETA_DAILY_BARS)
    assert {request[1:] for request in provider.requests} == {
        (CandleInterval.ONE_DAY, start, _CLOSE)
    }


@pytest.mark.anyio
async def test_reference_needs_no_read() -> None:
    """BTC in the book's quote is β 1 without any request."""
    provider = DailyBetaProvider()

    evidence = await load_beta_evidence(
        provider.service(), product_ids=("BTC-USDC",), as_of=_AS_OF, cache=BetaHistoryCache()
    )

    assert _estimate(evidence, "BTC-USDC").beta == Decimal(1)
    assert provider.requests == []


@pytest.mark.anyio
async def test_no_products_means_no_reads() -> None:
    """An empty request reads nothing and loads nothing."""
    provider = DailyBetaProvider(betas={"SOL-USDC": 1.5})

    evidence = await load_beta_evidence(
        provider.service(), product_ids=(), as_of=_AS_OF, cache=BetaHistoryCache()
    )

    assert evidence.results == {}
    assert provider.requests == []
    assert _unavailable(evidence, "SOL-USDC").reason is BetaUnavailableReason.NOT_LOADED


@pytest.mark.anyio
async def test_cache_reuses_a_complete_read_for_the_whole_day() -> None:
    """Later entries the same UTC day read nothing; the next settled day reads again."""
    provider = DailyBetaProvider(betas={"SOL-USDC": 1.5})
    cache = BetaHistoryCache()
    service = provider.service()

    await load_beta_evidence(service, product_ids=("SOL-USDC",), as_of=_AS_OF, cache=cache)
    await load_beta_evidence(
        service, product_ids=("SOL-USDC",), as_of=_AS_OF + timedelta(hours=11), cache=cache
    )
    assert len(provider.requests) == 2

    next_day = await load_beta_evidence(
        service, product_ids=("SOL-USDC",), as_of=_AS_OF + timedelta(days=1), cache=cache
    )
    assert len(provider.requests) == 4
    assert _estimate(next_day, "SOL-USDC").last_close == _CLOSE + timedelta(days=1)


@pytest.mark.anyio
async def test_failed_read_keeps_the_previous_complete_series() -> None:
    """An outage after a good day falls back to that day's series, which then ages."""
    provider = DailyBetaProvider(betas={"SOL-USDC": 1.5})
    cache = BetaHistoryCache()
    service = provider.service()
    await load_beta_evidence(service, product_ids=("SOL-USDC",), as_of=_AS_OF, cache=cache)
    provider.failing.add("SOL-USDC")

    later = _AS_OF + timedelta(days=1)
    evidence = await load_beta_evidence(
        service, product_ids=("SOL-USDC",), as_of=later, cache=cache
    )

    kept = _estimate(evidence, "SOL-USDC")
    assert kept.beta == Decimal("1.50")
    assert kept.last_close == _CLOSE
    assert fresh_beta(kept, as_of=later) is kept
    stale = fresh_beta(kept, as_of=_CLOSE + timedelta(hours=48, seconds=1))
    assert isinstance(stale, BetaUnavailable)
    assert stale.reason is BetaUnavailableReason.STALE


@pytest.mark.anyio
async def test_failed_read_without_history_is_fetch_failed() -> None:
    """With no earlier complete series, an outage is unknown evidence."""
    provider = DailyBetaProvider(betas={"SOL-USDC": 1.5}, failing={"SOL-USDC"})

    evidence = await load_beta_evidence(
        provider.service(), product_ids=("SOL-USDC",), as_of=_AS_OF, cache=BetaHistoryCache()
    )

    missing = _unavailable(evidence, "SOL-USDC")
    assert missing.reason is BetaUnavailableReason.FETCH_FAILED
    assert missing.describe() == "fetch_failed"


@pytest.mark.anyio
async def test_reference_outage_fails_every_product() -> None:
    """Without BTC history no product has a β."""
    provider = DailyBetaProvider(betas={"SOL-USDC": 1.5}, failing={"BTC-USDC"})

    evidence = await load_beta_evidence(
        provider.service(), product_ids=("SOL-USDC",), as_of=_AS_OF, cache=BetaHistoryCache()
    )

    assert _unavailable(evidence, "SOL-USDC").reason is BetaUnavailableReason.FETCH_FAILED


@pytest.mark.anyio
async def test_unlisted_product_is_fetch_failed() -> None:
    """A provider lookup error is unknown evidence, not an exception in the gate path."""
    provider = DailyBetaProvider()

    evidence = await load_beta_evidence(
        provider.service(), product_ids=("NEW-USDC",), as_of=_AS_OF, cache=BetaHistoryCache()
    )

    assert _unavailable(evidence, "NEW-USDC").reason is BetaUnavailableReason.FETCH_FAILED


@pytest.mark.anyio
async def test_failures_back_off_before_reading_again() -> None:
    """A failed read is not repeated on every entry; it is retried after the back-off."""
    provider = DailyBetaProvider(betas={"SOL-USDC": 1.5}, failing={"SOL-USDC"})
    cache = BetaHistoryCache()
    service = provider.service()

    for minutes in (0, 5, 14):
        await load_beta_evidence(
            service,
            product_ids=("SOL-USDC",),
            as_of=_AS_OF + timedelta(minutes=minutes),
            cache=cache,
        )
    assert provider.requested_products().count("SOL-USDC") == 1

    provider.failing.clear()
    recovered = await load_beta_evidence(
        service, product_ids=("SOL-USDC",), as_of=_AS_OF + BETA_RETRY_AFTER, cache=cache
    )
    assert provider.requested_products().count("SOL-USDC") == 2
    assert _estimate(recovered, "SOL-USDC").beta == Decimal("1.50")


@pytest.mark.anyio
async def test_incomplete_report_uses_its_bars_and_is_retried() -> None:
    """A range missing its newest bar still estimates from 89 returns, but is not final."""
    provider = DailyBetaProvider(betas={"SOL-USDC": 1.5}, incomplete={"SOL-USDC"})
    cache = BetaHistoryCache()
    service = provider.service()

    evidence = await load_beta_evidence(
        service, product_ids=("SOL-USDC",), as_of=_AS_OF, cache=cache
    )
    partial = _estimate(evidence, "SOL-USDC")
    assert partial.returns == 89
    assert partial.last_close == _CLOSE - timedelta(days=1)

    provider.incomplete.clear()
    await load_beta_evidence(
        service, product_ids=("SOL-USDC",), as_of=_AS_OF + BETA_RETRY_AFTER, cache=cache
    )
    assert provider.requested_products().count("SOL-USDC") == 2


@pytest.mark.anyio
async def test_short_history_is_insufficient() -> None:
    """A product listed 40 days ago has too few returns and fails closed."""
    provider = DailyBetaProvider(
        betas={"NEW-USDC": 1.0}, listed_at={"NEW-USDC": _CLOSE - timedelta(days=40)}
    )

    evidence = await load_beta_evidence(
        provider.service(), product_ids=("NEW-USDC",), as_of=_AS_OF, cache=BetaHistoryCache()
    )

    short = _unavailable(evidence, "NEW-USDC")
    assert short.reason is BetaUnavailableReason.INSUFFICIENT_HISTORY
    assert short.describe() == "insufficient_history n=39<60"


def test_bound_market_data_follows_the_scope() -> None:
    """Outside a risk scope there is no evidence source; inside it is the bound service."""
    service = DailyBetaProvider().service()
    assert bound_risk_market_data() is None
    with risk_market_data_scope(service):
        assert bound_risk_market_data() is service
    assert bound_risk_market_data() is None


def test_cache_is_bounded() -> None:
    """The LRU evicts the least recently used series beyond its bound."""
    cache = BetaHistoryCache(max_entries=2)
    for day in range(3):
        cache.store(
            "SOL-USDC",
            _CLOSE + timedelta(days=day),
            candles=(),
            complete=True,
            as_of=_AS_OF,
        )
    assert cache.lookup("SOL-USDC", _CLOSE, as_of=_AS_OF) is None
    assert cache.lookup("SOL-USDC", _CLOSE + timedelta(days=2), as_of=_AS_OF) is not None
