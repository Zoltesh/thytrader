"""Operator report models for market data, products, the data catalog, and indicators.

Covers candle freshness, the enabled spot product catalog, local dataset coverage,
and the implemented indicator catalog.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from thytrader.market_data.models import DATASET_TIMEFRAMES, DatasetTimeframe
from thytrader.operator.models import OperatorEnvelope, _FrozenModel
from thytrader.strategies.indicator_spec_model import ParameterKind


class MarketDataPayload(_FrozenModel):
    """Durable ingestion coverage for one product and dataset timeframe."""

    product_id: str
    provider: str | None
    timeframe: DatasetTimeframe = "1h"
    worker_status: str | None
    complete: bool | None
    freshness_status: str
    newest_candle_at: datetime | None
    age_seconds: int | None
    gap_count: int | None
    missing_intervals: int | None
    expected_candle_count: int | None
    received_candle_count: int | None
    content_fingerprint: str | None
    covered_starts_at: datetime | None
    covered_ends_at: datetime | None


class MarketDataReport(OperatorEnvelope):
    """Market-data freshness, gaps, and verified coverage."""

    report_kind: Literal["market_data"] = "market_data"
    payload: MarketDataPayload


class ProductSummary(_FrozenModel):
    """One enabled spot product from the current catalog, with its order constraints.

    Increments and minimum sizes are exact decimal strings straight from the venue
    catalog: an order quantity must be a multiple of ``base_increment`` and at least
    ``base_min_size``, a limit price a multiple of ``price_increment``, and a quote
    notional at least ``quote_min_size``. ``status`` is the venue status text
    (``online`` when trading normally; ``null`` when not reported) and ``alias`` names
    the product whose order book this one shares (``null`` for a standalone book).
    """

    product_id: str
    base_currency: str
    quote_currency: str
    trading_enabled: bool
    status: str | None
    alias: str | None
    price_increment: str
    base_increment: str
    quote_increment: str
    base_min_size: str
    quote_min_size: str


class ProductsPayload(_FrozenModel):
    """Coinbase or demo USD spot products visible to agents."""

    provider: str
    products: tuple[ProductSummary, ...]
    catalog_fingerprint: str | None = None
    catalog_observed_at: datetime | None = None


class ProductsReport(OperatorEnvelope):
    """Read-only USD spot catalog without secrets."""

    report_kind: Literal["products"] = "products"
    payload: ProductsPayload


class DatasetCoverageRow(_FrozenModel):
    """Local verified coverage plus watchlist and worker facts for one target.

    For a watched target, ``complete`` means the verified series spans the watch
    lookback (``watch_complete``); ``island_complete`` keeps the dataset-level fact. A
    two-minute dataset for a 90-day watch is not complete. Coverage is reported as
    ``watch_covered_candle_count`` of ``watch_expected_candle_count`` bars (with
    ``watch_coverage_ratio``). ``watch_sparsity`` is ``gapped`` when ``watch_complete``
    is false, even if the published island itself has zero gaps. ``watch_status``
    restates ``watch_complete`` as an operator noun so ``worker_status=succeeded``
    (latest chunk only) cannot be misread as a finished backfill.
    ``history_floor_at`` is set only when the listing search found no provider candle
    before the island (the market had not traded yet): coverage legitimately starts
    there and the watch counts as complete from that floor.
    ``synthetic_no_trade_intervals`` counts the flat zero-volume bars published for
    confirmed no-trade intervals (ADR 0095).
    """

    provider: str | None
    product_id: str
    timeframe: DatasetTimeframe
    watched: bool
    lookback_hours: int | None
    worker_status: str | None
    failure_code: str | None = None
    failure_message: str | None = None
    watch_complete: bool | None = None
    complete: bool | None
    freshness_status: str
    covered_starts_at: datetime | None
    covered_ends_at: datetime | None
    expected_candle_count: int | None
    received_candle_count: int | None
    gap_count: int | None
    missing_intervals: int | None
    content_fingerprint: str | None
    sparsity: Literal["none", "unknown", "gapped"]
    watch_sparsity: Literal["none", "unknown", "gapped"] | None = None
    watch_expected_candle_count: int | None = None
    watch_status: Literal["complete", "backfilling", "unknown"] | None = None
    history_floor_at: datetime | None = None
    island_complete: bool | None = None
    watch_covered_candle_count: int | None = None
    watch_coverage_ratio: float | None = None
    synthetic_no_trade_intervals: int | None = None


class DataCatalogPayload(_FrozenModel):
    """Agent-visible dataset catalog for Coinbase-listed complete-only coverage."""

    datasets: tuple[DatasetCoverageRow, ...]
    supported_timeframes: tuple[DatasetTimeframe, ...] = DATASET_TIMEFRAMES


class DataCatalogReport(OperatorEnvelope):
    """Local Parquet coverage joined with watchlist and worker state."""

    report_kind: Literal["data_catalog"] = "data_catalog"
    payload: DataCatalogPayload


class IndicatorParameterEntry(_FrozenModel):
    """One declared indicator parameter: bounds, builder default, and one-line help.

    Integer bounds/defaults are JSON numbers; decimal ones are canonical decimal
    strings. ``null`` bounds are unbounded. ``exclusive_minimum`` marks decimals that
    must be strictly greater than ``minimum``. ``optional`` parameters are omitted
    from documents unless set, and their ``default`` is ``null``.
    """

    name: str
    label: str
    value_type: Literal["integer", "decimal"]
    minimum: int | str | None
    maximum: int | str | None
    exclusive_minimum: bool = False
    default: int | str | None
    optional: bool = False
    help: str


class IndicatorCatalogEntry(_FrozenModel):
    """One implemented indicator kind and its canonical input/parameter shape.

    ``parameter_kind``, ``period_min``/``period_max`` (the integer-parameter bounds),
    and ``outputs`` keep their historical meaning; ``parameters`` is the complete,
    authoritative parameter list.
    """

    kind: str
    label: str
    category: Literal["trend", "momentum", "volatility", "volume", "statistical", "price"]
    summary: str
    inputs: tuple[str, ...]
    input_mode: Literal["configurable", "locked", "none"]
    default_input: str | tuple[str, ...] | None
    parameter_kind: ParameterKind = "period"
    period_min: int | None = None
    period_max: int | None = None
    parameters: tuple[IndicatorParameterEntry, ...] = ()
    constraints: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    warmup: str
    default_warmup_bars: int = Field(ge=1)
    supports_timeframe: bool
    supports_offset: bool
    supports_source: bool = Field(
        description=(
            "Whether the kind may read a reference instrument with `source` (ADR 0096); "
            "false only for constant."
        )
    )


class IndicatorsPayload(_FrozenModel):
    """Implemented indicator registry. Listed kinds are the only legal catalog."""

    indicators: tuple[IndicatorCatalogEntry, ...]


class IndicatorsReport(OperatorEnvelope):
    """Read-only list of strategy indicators the engine actually implements."""

    report_kind: Literal["indicators"] = "indicators"
    payload: IndicatorsPayload
