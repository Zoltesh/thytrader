"""Normalized broker event data, shared by capture and strict offline replay."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from thytrader.execution.broker import SubmitResult
from thytrader.market_data.models import Candle
from thytrader.trading.models import Fill, Order, OrderKind, OrderSide, OrderStatus

Operation = Literal["place", "cancel", "get", "fills", "match", "maker", "lookup"]


class BrokerRequest(BaseModel):
    """Only provider-neutral arguments; credentials and raw transport payloads have no field."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    client_order_id: str | None = None
    venue_order_id: str | None = None
    product_id: str | None = None
    side: OrderSide | None = None
    kind: OrderKind | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    stop_trigger_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    order_id: str | None = None
    submitted_at: datetime | None = None
    mark: Decimal | None = None
    order: Order | None = None
    candle: Candle | None = None


class BrokerEvent(BaseModel):
    """Response observation with actual call boundaries and a stable failure category."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    operation: Operation
    request: BrokerRequest
    started_at: datetime
    completed_at: datetime
    result: SubmitResult | None = None
    fills: tuple[Fill, ...] = ()
    fill: Fill | None = None
    maker_price: Decimal | None = None
    error: Literal["BrokerError", "TimeoutError", "ValueError"] | None = None


BrokerRequest.model_rebuild(_types_namespace={"Decimal": Decimal})
BrokerEvent.model_rebuild(_types_namespace={"OrderStatus": OrderStatus, "Decimal": Decimal})
