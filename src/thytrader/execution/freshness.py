"""Centralized entry prerequisites: candle age, product enablement, connection health."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from thytrader.market_data.freshness import FreshnessStatus, evaluate_freshness
from thytrader.market_data.models import (
    Candle,
    CandleInterval,
    MarketProduct,
    parse_candle_interval,
)
from thytrader.risk.models import RiskDecision, RiskReasonCode, RiskVerdict

_SIGNAL_GRACE_SECONDS = 300


def entry_prerequisites(
    *,
    product: MarketProduct,
    candle: Candle,
    now: datetime,
    timeframe: str,
    connection_healthy: bool = True,
    venue_balance_known: bool = True,
) -> RiskVerdict:
    """Deny a new entry when the product, candle, connection, or balance is unusable."""
    if not product.trading_enabled:
        return _deny(RiskReasonCode.PRODUCT_DISABLED, "Product is not a tradable USD spot market.")
    if not connection_healthy:
        return _deny(
            RiskReasonCode.CONNECTION_UNHEALTHY,
            "Required market-data or user-order connection is unhealthy.",
        )
    if not venue_balance_known:
        return _deny(
            RiskReasonCode.VENUE_BALANCE_UNKNOWN,
            "Venue quote balance is unknown; new entries are disabled.",
        )
    interval = parse_candle_interval(timeframe)
    freshness = evaluate_freshness(
        product_id=product.product_id,
        newest_candle_at=candle.starts_at,
        now=now,
        interval=interval,
    )
    if freshness.status is not FreshnessStatus.FRESH:
        return _deny(
            RiskReasonCode.STALE_MARK,
            "Latest expected close is stale; a fresh last-close mark is required.",
        )
    return RiskVerdict(
        decision=RiskDecision.ALLOW,
        reason_code=RiskReasonCode.ALLOWED,
        detail="Risk policy allows this action.",
    )


def signal_still_valid(
    *,
    candle: Candle,
    timeframe: str,
    now: datetime,
    current_quote: Decimal | None = None,
) -> bool:
    """True when the latest closed-bar signal is still within max age and quote checks."""
    interval = parse_candle_interval(timeframe)
    age = (now - candle.starts_at).total_seconds()
    max_age = int(interval.duration.total_seconds()) * 2 + _SIGNAL_GRACE_SECONDS
    if age < 0 or age >= max_age:
        return False
    if current_quote is not None and current_quote <= 0:
        return False
    return True


def marketable_quote_mark(*, candle: Candle, venue_price: Decimal | None) -> Decimal:
    """Use a current venue price for quote-budget marketable orders, not a stale close."""
    if venue_price is not None and venue_price > 0:
        return venue_price
    return candle.close


def _deny(reason_code: RiskReasonCode, detail: str) -> RiskVerdict:
    """Return a fail-closed freshness verdict."""
    return RiskVerdict(decision=RiskDecision.DENY, reason_code=reason_code, detail=detail)
