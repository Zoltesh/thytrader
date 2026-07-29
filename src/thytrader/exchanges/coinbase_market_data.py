"""Coinbase Advanced Trade adapter for public product and historical-candle data."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Protocol

from thytrader.market_data.models import (
    Candle,
    CandleInterval,
    CandleRangeReport,
    MarketDataPreview,
    MarketProduct,
)
from thytrader.market_data.quality import CandleQualityError, analyze_candles, analyze_range

if TYPE_CHECKING:
    from collections.abc import Mapping

_CANDLE_PAGE_LIMIT = 350
_RECENT_INTERVAL_COUNT = 25
_MAX_RANGE_INTERVAL_COUNT = 2_160
_COINBASE_GRANULARITIES: dict[CandleInterval, str] = {
    CandleInterval.ONE_HOUR: "ONE_HOUR",
}


class CoinbaseMarketDataError(ValueError):
    """Signal malformed Coinbase market-data responses without partial results."""


class CoinbaseResponse(Protocol):
    """Minimal response behavior used from the official Coinbase SDK."""

    def to_dict(self) -> dict[str, Any]:
        """Convert one SDK response to untrusted plain Python data."""
        ...


class CoinbaseMarketDataClient(Protocol):
    """Public Coinbase REST methods needed for the market-data preview."""

    def get_products(
        self,
        limit: int | None = None,
        offset: int | None = None,
        product_type: str | None = None,
        product_ids: list[str] | None = None,
        contract_expiry_type: str | None = None,
        expiring_contract_status: str | None = None,
        get_tradability_status: bool | None = False,
        get_all_products: bool | None = False,
    ) -> CoinbaseResponse:
        """Return a bounded or complete product catalog from the official SDK."""
        ...

    def get_product(self, product_id: str) -> CoinbaseResponse:
        """Return one product's metadata and exchange constraints."""
        ...

    def get_candles(
        self,
        product_id: str,
        start: str,
        end: str,
        granularity: str,
        limit: int | None = None,
    ) -> CoinbaseResponse:
        """Return a bounded page of product candles."""
        ...


class CoinbaseMarketData:
    """Expose validated Coinbase public market data through domain models."""

    def __init__(self, client: CoinbaseMarketDataClient) -> None:
        """Initialize the adapter around an official public or authenticated SDK client."""
        self._client = client

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """Fetch and validate Coinbase's full tradability-aware spot product catalog."""
        response = await asyncio.to_thread(
            self._client.get_products,
            product_type="SPOT",
            get_tradability_status=True,
            get_all_products=True,
        )
        return _parse_products(response.to_dict())

    async def get_recent_preview(
        self,
        product_id: str,
        interval: CandleInterval,
        now: datetime,
    ) -> MarketDataPreview:
        """Fetch product constraints and recent completed candles for one spot product."""
        _require_utc(now)
        start = now - interval.duration * _RECENT_INTERVAL_COUNT
        product_response = await asyncio.to_thread(self._client.get_product, product_id)
        candle_response = await asyncio.to_thread(
            self._client.get_candles,
            product_id,
            str(int(start.timestamp())),
            str(int(now.timestamp())),
            _COINBASE_GRANULARITIES[interval],
            _CANDLE_PAGE_LIMIT,
        )
        product = _parse_product(product_response.to_dict())
        candles = _parse_candles(candle_response.to_dict())
        try:
            quality = analyze_candles(candles, interval, now)
        except CandleQualityError as error:
            raise CoinbaseMarketDataError(str(error)) from error
        return MarketDataPreview(product=product, interval=interval, as_of=now, quality=quality)

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Retrieve one bounded closed-candle range through non-overlapping Coinbase pages."""
        _require_utc(starts_at)
        _require_utc(ends_at)
        _require_utc(now)
        interval_count = (ends_at - starts_at) // interval.duration
        if starts_at >= ends_at or ends_at > now or interval_count > _MAX_RANGE_INTERVAL_COUNT:
            message = "Historical range is outside the supported closed-candle request bounds."
            raise CoinbaseMarketDataError(message)
        product_response = await asyncio.to_thread(self._client.get_product, product_id)
        _parse_product(product_response.to_dict())
        candles: list[Candle] = []
        page_start = starts_at
        while page_start < ends_at:
            page_end = min(page_start + interval.duration * _CANDLE_PAGE_LIMIT, ends_at)
            response = await asyncio.to_thread(
                self._client.get_candles,
                product_id,
                str(int(page_start.timestamp())),
                str(int(page_end.timestamp())),
                _COINBASE_GRANULARITIES[interval],
                _CANDLE_PAGE_LIMIT,
            )
            page_candles = _parse_candles(response.to_dict())
            candles.extend(candle for candle in page_candles if candle.starts_at != page_end)
            page_start = page_end
        try:
            return analyze_range(tuple(candles), interval, starts_at, ends_at, now)
        except CandleQualityError as error:
            raise CoinbaseMarketDataError(str(error)) from error


def _parse_product(payload: dict[str, Any]) -> MarketProduct:
    """Validate an untrusted Coinbase product response into exact venue constraints."""
    product_id = _required_text(payload, "product_id")
    base_currency = _required_text(payload, "base_currency_id")
    quote_currency = _required_text(payload, "quote_currency_id")
    return MarketProduct(
        product_id=product_id,
        base_currency=base_currency,
        quote_currency=quote_currency,
        price_increment=_positive_decimal(payload, "price_increment"),
        base_increment=_positive_decimal(payload, "base_increment"),
        quote_increment=_positive_decimal(payload, "quote_increment"),
        base_min_size=_positive_decimal(payload, "base_min_size"),
        quote_min_size=_positive_decimal(payload, "quote_min_size"),
        trading_enabled=(
            payload.get("is_disabled") is not True and payload.get("trading_disabled") is not True
        ),
    )


def _parse_products(payload: dict[str, Any]) -> tuple[MarketProduct, ...]:
    """Validate every product in a Coinbase catalog without silently omitting bad rows."""
    raw_products = payload.get("products")
    if not isinstance(raw_products, list):
        message = "Coinbase product response did not include a product list."
        raise CoinbaseMarketDataError(message)
    products: list[MarketProduct] = []
    for raw_product in raw_products:
        if not isinstance(raw_product, dict):
            message = "Coinbase product response included a non-object product."
            raise CoinbaseMarketDataError(message)
        product_payload: dict[str, object] = {}
        for key, value in raw_product.items():
            if not isinstance(key, str):
                message = "Coinbase product response included a non-text field name."
                raise CoinbaseMarketDataError(message)
            product_payload[key] = value
        products.append(_parse_product(product_payload))
    return tuple(products)


def _parse_candles(payload: dict[str, Any]) -> tuple[Candle, ...]:
    """Validate every Coinbase candle or fail closed rather than omit malformed values."""
    raw_candles = payload.get("candles")
    if not isinstance(raw_candles, list):
        message = "Coinbase candle response did not include a candle list."
        raise CoinbaseMarketDataError(message)
    return tuple(_parse_candle(raw_candle) for raw_candle in raw_candles)


def _parse_candle(raw_candle: object) -> Candle:
    """Parse one exact Coinbase OHLCV mapping with complete invariants."""
    if not isinstance(raw_candle, dict):
        message = "Coinbase candle response included a non-object candle."
        raise CoinbaseMarketDataError(message)
    payload: dict[str, object] = {}
    for key, value in raw_candle.items():
        if not isinstance(key, str):
            message = "Coinbase candle response included a non-text field name."
            raise CoinbaseMarketDataError(message)
        payload[key] = value
    start = payload.get("start")
    if not isinstance(start, str) or not start.isdigit():
        message = "Coinbase candle start must be an epoch-seconds string."
        raise CoinbaseMarketDataError(message)
    candle = Candle(
        starts_at=datetime.fromtimestamp(int(start), tz=UTC),
        open=_positive_decimal(payload, "open"),
        high=_positive_decimal(payload, "high"),
        low=_positive_decimal(payload, "low"),
        close=_positive_decimal(payload, "close"),
        volume=_non_negative_decimal(payload, "volume"),
    )
    if candle.low > min(candle.open, candle.close) or candle.high < max(candle.open, candle.close):
        message = "Coinbase candle OHLC values are internally inconsistent."
        raise CoinbaseMarketDataError(message)
    return candle


def _required_text(payload: Mapping[str, object], field: str) -> str:
    """Return one required non-empty text field from an untrusted response."""
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        message = f"Coinbase response field {field!r} must be a non-empty string."
        raise CoinbaseMarketDataError(message)
    return value


def _positive_decimal(payload: Mapping[str, object], field: str) -> Decimal:
    """Return one finite positive decimal-string field from an upstream payload."""
    value = _decimal(payload, field)
    if value <= 0:
        message = f"Coinbase response field {field!r} must be positive."
        raise CoinbaseMarketDataError(message)
    return value


def _non_negative_decimal(payload: Mapping[str, object], field: str) -> Decimal:
    """Return one finite non-negative decimal-string field from an upstream payload."""
    value = _decimal(payload, field)
    if value < 0:
        message = f"Coinbase response field {field!r} must be non-negative."
        raise CoinbaseMarketDataError(message)
    return value


def _decimal(payload: Mapping[str, object], field: str) -> Decimal:
    """Parse one finite decimal-string field without binary floating point."""
    raw = payload.get(field)
    if not isinstance(raw, str):
        message = f"Coinbase response field {field!r} must be a decimal string."
        raise CoinbaseMarketDataError(message)
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        message = f"Coinbase response field {field!r} is not a valid decimal."
        raise CoinbaseMarketDataError(message) from error
    if not value.is_finite():
        message = f"Coinbase response field {field!r} must be finite."
        raise CoinbaseMarketDataError(message)
    return value


def _require_utc(value: datetime) -> None:
    """Reject a naive or non-UTC clock value before issuing a historical query."""
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        message = "Market-data requests require a timezone-aware UTC clock value."
        raise CoinbaseMarketDataError(message)
