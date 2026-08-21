"""Unit tests for market data candle freshness contracts and evaluator."""

from datetime import UTC, datetime, timedelta

import pytest

from thytrader.market_data.freshness import (
    FreshnessStatus,
    evaluate_freshness,
)


def test_evaluate_freshness_boundary_conditions() -> None:
    """Threshold 2h05m (7500s) determines fresh vs stale precisely."""
    now = datetime(2026, 8, 17, 14, 0, 0, tzinfo=UTC)

    # 2h 4m 59s ago -> 7499s (FRESH)
    candle_fresh = now - timedelta(seconds=7499)
    res_fresh = evaluate_freshness(product_id="BTC-USD", newest_candle_at=candle_fresh, now=now)
    assert res_fresh.status == FreshnessStatus.FRESH
    assert res_fresh.age_seconds == 7499

    # 2h 5m 00s ago -> 7500s (STALE)
    candle_stale = now - timedelta(seconds=7500)
    res_stale = evaluate_freshness(product_id="BTC-USD", newest_candle_at=candle_stale, now=now)
    assert res_stale.status == FreshnessStatus.STALE
    assert res_stale.age_seconds == 7500

    # None -> UNKNOWN
    res_unknown = evaluate_freshness(product_id="BTC-USD", newest_candle_at=None, now=now)
    assert res_unknown.status == FreshnessStatus.UNKNOWN
    assert res_unknown.age_seconds is None
    assert res_unknown.newest_candle_at is None


def test_evaluate_freshness_rejects_naive_datetime() -> None:
    """Naive datetimes are rejected for both observation and candle timestamps."""
    now = datetime(2026, 8, 17, 14, 0, 0, tzinfo=UTC)
    candle = datetime(2026, 8, 17, 13, 0, 0, tzinfo=UTC)

    # Test passing a naive datetime object via a custom subclass
    class NaiveDateTime(datetime):
        @property
        def tzinfo(self) -> None:
            return None

    naive_now = NaiveDateTime(2026, 8, 17, 14, 0, 0, tzinfo=UTC)
    naive_candle = NaiveDateTime(2026, 8, 17, 13, 0, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="UTC"):
        evaluate_freshness(product_id="BTC-USD", newest_candle_at=candle, now=naive_now)

    with pytest.raises(ValueError, match="UTC"):
        evaluate_freshness(
            product_id="BTC-USD",
            newest_candle_at=naive_candle,
            now=now,
        )
