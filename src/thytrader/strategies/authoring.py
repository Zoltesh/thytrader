"""Server-owned strategy identities and fail-closed template definitions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader.execution.ids import uuid7
from thytrader.market_data.models import (
    EXECUTION_TIMEFRAMES,
    as_dataset_timeframe,
    parse_candle_interval,
)
from thytrader.market_data.products import parse_spot_product_id
from thytrader.strategies.models import Instrument, StrategyDefinition
from thytrader.strategies.templates import build_template_definition, parse_template_id

if TYPE_CHECKING:
    from uuid import UUID


def new_strategy_identity(now: datetime | None = None) -> tuple[UUID, datetime]:
    """Mint one UUIDv7 strategy id and its millisecond-truncated UTC creation instant."""
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    created_at = created_at.replace(microsecond=(created_at.microsecond // 1_000) * 1_000)
    return uuid7(created_at), created_at


def create_template_strategy(
    *,
    now: datetime | None = None,
    product_id: str = "BTC-USD",
    timeframe: str = "1h",
    template: str = "ema-trend",
) -> StrategyDefinition:
    """Construct one server-identified strategy definition from a fail-closed template."""
    strategy_id, created_at = new_strategy_identity(now)
    try:
        clock = as_dataset_timeframe(parse_candle_interval(timeframe))
    except ValueError as error:
        allowed = ", ".join(EXECUTION_TIMEFRAMES)
        message = f"Template strategies support only ingested venue timeframes: {allowed}."
        raise ValueError(message) from error
    template_id = parse_template_id(template)
    instrument = _instrument_for_product(product_id)
    return build_template_definition(
        template_id=template_id,
        strategy_id=strategy_id,
        created_at=created_at,
        instrument=instrument,
        timeframe=clock,
    )


def _instrument_for_product(product_id: str) -> Instrument:
    """Build a USD or USDC spot instrument from a product id such as ETH-USDC."""
    base, quote = parse_spot_product_id(product_id)
    normalized = f"{base}-{quote}"
    return Instrument(product_id=normalized, base_currency=base, quote_currency=quote)
