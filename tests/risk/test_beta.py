"""Pure BTC-beta estimation from settled daily candles (ADR 0125)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import math

import pytest

from thytrader.market_data.models import Candle
from thytrader.risk.beta import (
    BETA_STALE_AFTER,
    MIN_BETA_RETURNS,
    BetaEstimate,
    BetaUnavailable,
    BetaUnavailableReason,
    beta_reference,
    estimate_beta,
    fresh_beta,
    reference_beta,
)

_START = datetime(2026, 7, 1, tzinfo=UTC)
_DAY = timedelta(days=1)


def _reference_returns(count: int) -> list[float]:
    """Return a deterministic, non-constant daily log-return series."""
    return [0.03 * math.sin(index * 0.7) + 0.01 * math.cos(index * 1.3) for index in range(count)]


def _series(returns: list[float], *, start: datetime = _START, base: float = 100.0) -> list[Candle]:
    """Build daily candles whose closes follow ``returns`` from ``base``."""
    closes = [base]
    for value in returns:
        closes.append(closes[-1] * math.exp(value))
    return [_candle(start + index * _DAY, close) for index, close in enumerate(closes)]


def _candle(starts_at: datetime, close: float | Decimal) -> Candle:
    """Build one daily candle with only the close mattering."""
    price = close if isinstance(close, Decimal) else Decimal(repr(close))
    return Candle(
        starts_at=starts_at, open=price, high=price, low=price, close=price, volume=Decimal(1)
    )


def _scaled(factor: float, count: int = 90) -> tuple[list[Candle], list[Candle]]:
    """Return product and reference candles where r_product = factor x r_reference."""
    reference = _reference_returns(count)
    return _series([factor * value for value in reference], base=20.0), _series(reference)


def test_beta_of_a_scaled_series_is_the_scale() -> None:
    """r_p = 1.5 r_btc gives exactly 1.50 despite float noise."""
    product, reference = _scaled(1.5)

    result = estimate_beta("SOL-USDC", product, reference)

    assert isinstance(result, BetaEstimate)
    assert result.beta == Decimal("1.50")
    assert result.reference_id == "BTC-USDC"
    assert result.returns == 90
    assert result.last_close == reference[-1].starts_at + _DAY


def test_beta_rounds_up_to_the_cent() -> None:
    """A slope of 1.234 is reported conservatively as 1.24."""
    product, reference = _scaled(1.234)

    result = estimate_beta("SOL-USDC", product, reference)

    assert isinstance(result, BetaEstimate)
    assert result.beta == Decimal("1.24")


@pytest.mark.parametrize(("factor", "expected"), [(-0.8, Decimal("0.00")), (4.2, Decimal("3.00"))])
def test_beta_is_clamped_to_zero_and_three(factor: float, expected: Decimal) -> None:
    """A negative slope floors at 0 and a huge one caps at 3."""
    product, reference = _scaled(factor)

    result = estimate_beta("DOGE-USDC", product, reference)

    assert isinstance(result, BetaEstimate)
    assert result.beta == expected


def test_reference_product_has_beta_one_without_history() -> None:
    """BTC in the entry's quote is the reference: β = 1 and it never goes stale."""
    result = estimate_beta("BTC-USDC", [], [])

    assert result == reference_beta("BTC-USDC")
    assert isinstance(result, BetaEstimate)
    assert result.beta == Decimal(1)
    assert result.last_close is None
    assert fresh_beta(result, as_of=_START + timedelta(days=400)) is result


def test_reference_follows_the_quote_currency() -> None:
    """A USD product is measured against BTC-USD, never a different quote."""
    assert beta_reference("ETH-USD") == "BTC-USD"
    assert beta_reference("ETH-USDT") == "BTC-USDT"
    assert estimate_beta("BTC-USD", [], []) == reference_beta("BTC-USD")


def test_malformed_product_id_is_rejected() -> None:
    """A non-spot product id has no reference and raises rather than guessing one."""
    with pytest.raises(ValueError, match=r".+"):
        beta_reference("not-a-product")


def test_fewer_than_sixty_returns_is_insufficient() -> None:
    """59 aligned returns fail closed with the count in the cause."""
    product, reference = _scaled(1.2, count=MIN_BETA_RETURNS - 1)

    result = estimate_beta("ADA-USDC", product, reference)

    assert isinstance(result, BetaUnavailable)
    assert result.reason is BetaUnavailableReason.INSUFFICIENT_HISTORY
    assert result.returns == MIN_BETA_RETURNS - 1
    assert result.describe() == "insufficient_history n=59<60"


def test_exactly_sixty_returns_is_enough() -> None:
    """The minimum is inclusive."""
    product, reference = _scaled(1.2, count=MIN_BETA_RETURNS)

    result = estimate_beta("ADA-USDC", product, reference)

    assert isinstance(result, BetaEstimate)
    assert result.returns == MIN_BETA_RETURNS


def test_a_gap_drops_only_the_returns_that_span_it() -> None:
    """A missing product bar removes the two returns touching it, nothing else."""
    product, reference = _scaled(1.5)
    gapped = [candle for index, candle in enumerate(product) if index != 40]

    result = estimate_beta("SOL-USDC", gapped, reference)

    assert isinstance(result, BetaEstimate)
    assert result.returns == 88
    assert result.beta == Decimal("1.50")


def test_a_missing_reference_bar_is_also_a_gap() -> None:
    """Alignment needs both series; a reference gap drops returns the same way."""
    product, reference = _scaled(1.5)
    gapped = [candle for index, candle in enumerate(reference) if index != 10]

    result = estimate_beta("SOL-USDC", product, gapped)

    assert isinstance(result, BetaEstimate)
    assert result.returns == 88


def test_gaps_can_make_history_insufficient() -> None:
    """Every other bar missing leaves no adjacent pair at all."""
    product, reference = _scaled(1.5)
    sparse = product[::2]

    result = estimate_beta("SOL-USDC", sparse, reference)

    assert isinstance(result, BetaUnavailable)
    assert result.reason is BetaUnavailableReason.INSUFFICIENT_HISTORY
    assert result.returns == 0


def test_only_the_ninety_day_lookback_is_used() -> None:
    """Older history beyond 90 days of the newest common bar is ignored."""
    reference_values = _reference_returns(150)
    product_values = [
        (3.0 if index < 60 else 1.5) * value for index, value in enumerate(reference_values)
    ]

    result = estimate_beta("SOL-USDC", _series(product_values), _series(reference_values))

    assert isinstance(result, BetaEstimate)
    assert result.returns == 90
    assert result.beta == Decimal("1.50")


def test_non_positive_closes_are_skipped() -> None:
    """A zero close is not a price: the bar is dropped as a gap."""
    product, reference = _scaled(1.5)
    product[30] = _candle(product[30].starts_at, Decimal(0))

    result = estimate_beta("SOL-USDC", product, reference)

    assert isinstance(result, BetaEstimate)
    assert result.returns == 88


def test_conflicting_duplicate_bars_make_history_invalid() -> None:
    """Two different closes for one bar cannot both be true."""
    product, reference = _scaled(1.5)
    product.append(_candle(product[5].starts_at, product[5].close + 1))

    result = estimate_beta("SOL-USDC", product, reference)

    assert isinstance(result, BetaUnavailable)
    assert result.reason is BetaUnavailableReason.INVALID_HISTORY
    assert result.describe() == "invalid_history"


def test_identical_duplicate_bars_are_harmless() -> None:
    """A repeated identical bar is the same evidence."""
    product, reference = _scaled(1.5)
    product.append(product[5])

    result = estimate_beta("SOL-USDC", product, reference)

    assert isinstance(result, BetaEstimate)
    assert result.returns == 90


def test_a_constant_reference_is_invalid() -> None:
    """Zero reference variance has no slope."""
    flat = _series([0.0] * 90)
    product = _series(_reference_returns(90))

    result = estimate_beta("SOL-USDC", product, flat)

    assert isinstance(result, BetaUnavailable)
    assert result.reason is BetaUnavailableReason.INVALID_HISTORY


def test_fresh_beta_allows_up_to_forty_eight_hours() -> None:
    """The boundary is inclusive; one second later the estimate is stale."""
    product, reference = _scaled(1.5)
    estimate = estimate_beta("SOL-USDC", product, reference)
    assert isinstance(estimate, BetaEstimate)
    assert estimate.last_close is not None
    edge = estimate.last_close + BETA_STALE_AFTER

    assert fresh_beta(estimate, as_of=edge) is estimate
    stale = fresh_beta(estimate, as_of=edge + timedelta(seconds=1))

    assert isinstance(stale, BetaUnavailable)
    assert stale.reason is BetaUnavailableReason.STALE
    assert stale.last_close == estimate.last_close
    assert stale.describe() == f"stale last_close={estimate.last_close.isoformat()}"


def test_fresh_beta_passes_unavailable_through() -> None:
    """An already unavailable result is returned unchanged."""
    missing = BetaUnavailable(
        product_id="SOL-USDC",
        reference_id="BTC-USDC",
        reason=BetaUnavailableReason.FETCH_FAILED,
    )

    assert fresh_beta(missing, as_of=_START) is missing
    assert missing.describe() == "fetch_failed"
