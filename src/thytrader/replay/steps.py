"""Typed as-seen inputs to the existing isolated execution orchestration."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from thytrader.exchanges.fees import FeeProfile
from thytrader.market_data.models import Candle, DatasetTimeframe, MarketProduct
from thytrader.replay.broker_events import BrokerEvent
from thytrader.replay.domain_inputs import restore_utc
from thytrader.risk.models import RiskPolicyDefinition
from thytrader.runtime_inputs import RuntimeInput
from thytrader.trading.day_open import DailyOpeningEvidence
from thytrader.trading.models import (
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    Order,
    Position,
    RuntimePhase,
)


class StepModel(BaseModel):
    """Reject undeclared fields and coercion on normalized evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CandleWindow(StepModel):
    """Exact normalized completed bars on a named auxiliary clock."""

    key: str
    product_id: str
    timeframe: DatasetTimeframe
    candles: tuple[Candle, ...] = Field(max_length=20000)


class ExecutionOutcome(StepModel):
    """Full order/fill and position economics, independently recomputed from inputs."""

    cash: Decimal
    deployment_status: DeploymentStatus = DeploymentStatus.RUNNING
    daily_loss_latched: bool = False
    drawdown_latched: bool = False
    phase: RuntimePhase
    positions: tuple[Position, ...]
    orders: tuple[Order, ...]
    fills: tuple[Fill, ...]


class TimingEvidence(StepModel):
    """Integer nanosecond observations; absent spans remain absent, never inferred from fills."""

    elapsed_ns: int = Field(ge=0)
    computation_cpu_ns: int = Field(ge=0)
    database_calls: int | None = Field(default=None, ge=0)
    database_elapsed_ns: int | None = Field(default=None, ge=0)
    venue_calls: int | None = Field(default=None, ge=0)
    venue_elapsed_ns: int | None = Field(default=None, ge=0)
    queue_wait_ns: int | None = None
    unavailable_reasons: tuple[str, ...]


class ReplayStep(StepModel):
    """One exact book, completed window, risk policy and clock/identity tape."""

    operation: str
    before: DeploymentSnapshot
    product: MarketProduct
    data_available_at: datetime
    evaluation_started_at: datetime
    decision_completed_at: datetime
    timing: TimingEvidence | None = None
    candles: tuple[Candle, ...] = Field(max_length=20000)
    htf_candles: tuple[Candle, ...] = ()
    indicator_windows: tuple[CandleWindow, ...] = ()
    reference_windows: tuple[CandleWindow, ...] = ()
    portfolio: tuple[DeploymentSnapshot, ...] = ()
    marks: tuple[tuple[str, Decimal], ...] = ()
    live_base_available: Decimal | None = None
    fee_profile: FeeProfile | None = None
    broker_supports_client_lookup: bool = False
    risk_policy: RiskPolicyDefinition
    allow_new_entries: bool
    entry_inhibited: bool = True
    runtime_inputs: tuple[RuntimeInput, ...] = Field(max_length=5000)
    broker_events: tuple[BrokerEvent, ...] = Field(default=(), max_length=5000)
    expected: ExecutionOutcome

    @field_validator("*", mode="after")
    @classmethod
    def domain_utc(cls, value: object) -> object:
        """Keep the existing domain UTC singleton without modifying live timestamp consumers."""
        return restore_utc(value)

    @field_validator("fee_profile", mode="before")
    @classmethod
    def fee_profile_utc(cls, value: object) -> object:
        """Restore exact UTC singleton required by the existing domain fee profile from JSON."""
        if isinstance(value, dict):
            # This is the normalized JSON boundary, not a business-state dictionary.
            fields = dict(value)
            instant = fields.get("as_of")
            if isinstance(instant, str):
                parsed = datetime.fromisoformat(instant)
                if parsed.utcoffset() != timedelta(0):
                    raise ValueError("fee profile requires UTC")
                fields["as_of"] = parsed.astimezone(UTC)
            return fields
        return value


CandleWindow.model_rebuild(_types_namespace={"Decimal": Decimal, "datetime": datetime})
ReplayStep.model_rebuild(
    _types_namespace={
        "DailyOpeningEvidence": DailyOpeningEvidence,
        "Decimal": Decimal,
        "datetime": datetime,
        "UUID": UUID,
    }
)


def comparable(actual: ExecutionOutcome, expected: ExecutionOutcome) -> ExecutionOutcome:
    """``actual`` without order fields a capture from before they existed never recorded.

    Orders gained their cancel time and settlement in ADR 0141. A package captured earlier has
    neither; the replayed engine records them, so they are dropped from an actual order only
    when the expected order with the same id has them unset.
    """
    unset = {
        order.id
        for order in expected.orders
        if order.canceled_at is None and order.settled_at is None
    }
    orders = tuple(
        replace(order, canceled_at=None, settled_at=None) if order.id in unset else order
        for order in actual.orders
    )
    return actual.model_copy(update={"orders": orders})
