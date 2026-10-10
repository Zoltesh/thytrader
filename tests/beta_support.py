"""Hermetic daily-candle provider for BTC-beta evidence tests (ADR 0125)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import math

from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import (
    Candle,
    CandleInterval,
    CandleRangeReport,
    MarketDataPreview,
    MarketProduct,
)
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.service import MarketDataService
from thytrader.risk.beta_evidence import BETA_DAILY_BARS
from thytrader.risk.models import RiskPolicyDefinition, compiled_default_risk_policy

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def reference_return(day: int) -> float:
    """A deterministic, non-constant daily BTC log return for day ``day`` since the epoch."""
    return 0.03 * math.sin(day * 0.7) + 0.01 * math.cos(day * 1.3)


@dataclass
class DailyBetaProvider:
    """Serve daily closes where each product's log return is ``beta`` x BTC's.

    ``betas`` maps a product to its true β; a BTC product is the reference (β 1). Products
    in ``failing`` raise, products in ``incomplete`` drop their newest bar, products in
    ``listed_at`` have no bars before that instant, and products absent from ``betas``
    raise like an unlisted market. ``requests`` records every
    ``(product, interval, starts_at, ends_at)`` asked for.
    """

    betas: dict[str, float] = field(default_factory=dict)
    failing: set[str] = field(default_factory=set)
    incomplete: set[str] = field(default_factory=set)
    listed_at: dict[str, datetime] = field(default_factory=dict)
    requests: list[tuple[str, CandleInterval, datetime, datetime]] = field(default_factory=list)

    def service(self) -> MarketDataService:
        """Wrap this provider in a real market-data service."""
        return MarketDataService(self)

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """No catalog is needed for range reads."""
        return ()

    async def get_recent_preview(
        self, product_id: str, interval: CandleInterval, now: datetime
    ) -> MarketDataPreview:
        """β evidence must use explicit historical ranges."""
        del product_id, interval, now
        raise AssertionError("BTC-beta evidence must request an explicit historical range.")

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Return the requested daily closes, or fail as configured."""
        self.requests.append((product_id, interval, starts_at, ends_at))
        if product_id in self.failing:
            raise RuntimeError(f"simulated outage for {product_id}")
        beta = 1.0 if product_id.startswith("BTC-") else self.betas.get(product_id)
        if beta is None:
            raise LookupError(f"{product_id} is not listed")
        count = (ends_at - starts_at) // interval.duration
        candles = [
            _daily_candle(starts_at + interval.duration * offset, beta) for offset in range(count)
        ]
        if product_id in self.incomplete:
            candles = candles[:-1]
        listed = self.listed_at.get(product_id)
        if listed is not None:
            candles = [candle for candle in candles if candle.starts_at >= listed]
        return analyze_range(tuple(candles), interval, starts_at, ends_at, now)

    def requested_products(self) -> list[str]:
        """Return the products read, in request order."""
        return [item[0] for item in self.requests]


def _daily_candle(starts_at: datetime, beta: float) -> Candle:
    """Close at day ``n`` is 100 x exp(beta x Σ reference returns up to n)."""
    day = (starts_at - _EPOCH) // timedelta(days=1)
    total = sum(reference_return(index) for index in range(day + 1))
    close = Decimal(repr(100.0 * math.exp(beta * total)))
    return Candle(
        starts_at=starts_at, open=close, high=close, low=close, close=close, volume=Decimal(1)
    )


@dataclass
class DemoWithDailyBeta:
    """Demo candles for execution, plus ``daily`` for the β loader's 91-day daily ranges.

    Only reads shaped like the β loader's (the day interval, ``BETA_DAILY_BARS`` long) reach
    ``daily``, so ``daily.requests`` counts β evidence reads and nothing else.
    """

    daily: DailyBetaProvider
    base: DemoMarketData = field(default_factory=DemoMarketData)

    def service(self) -> MarketDataService:
        """Wrap this provider in a real market-data service."""
        return MarketDataService(self)

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """The demo catalog."""
        return await self.base.list_products()

    async def get_recent_preview(
        self, product_id: str, interval: CandleInterval, now: datetime
    ) -> MarketDataPreview:
        """Demo previews."""
        return await self.base.get_recent_preview(product_id, interval, now)

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Route β-shaped daily ranges to ``daily`` and everything else to the demo."""
        if (
            interval is CandleInterval.ONE_DAY
            and ends_at - starts_at == interval.duration * BETA_DAILY_BARS
        ):
            return await self.daily.get_historical_range(
                product_id, interval, starts_at, ends_at, now
            )
        return await self.base.get_historical_range(product_id, interval, starts_at, ends_at, now)


def beta_policy(*, fraction: str | None = "1", quote: str | None = None) -> RiskPolicyDefinition:
    """A published-style wide policy with only the β cap fields set as given."""
    return RiskPolicyDefinition.model_validate(
        {
            **compiled_default_risk_policy().model_dump(mode="python"),
            "version": 2,
            "max_portfolio_exposure_fraction": "1",
            "per_product_max_exposure_fraction": "1",
            "max_btc_beta_exposure_fraction": fraction,
            "max_btc_beta_exposure_quote": quote,
        }
    )
