"""Durable why-trade records composed from frozen intent attribution and live ledger facts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from thytrader.market_data.products import SPOT_PRODUCT_ID_PATTERN

TRADE_REASON_SCHEMA_VERSION: Literal["thytrader-trade-reason-v1"] = "thytrader-trade-reason-v1"
_FINGERPRINT = r"^sha256:[0-9a-f]{64}$"
_TIMEFRAME = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d")


class TradeReasonOrigin(StrEnum):
    """Who created the order intent this why-trade record explains."""

    HUMAN = "human"
    AGENT = "agent"
    RUNTIME = "runtime"


class TradeReasonNoteOrigin(StrEnum):
    """Who authored a discretionary or review note. Runtime cannot journal notes."""

    HUMAN = "human"
    AGENT = "agent"


class TradeReasonSignalKind(StrEnum):
    """Why the runtime or operator created this intent. Not a reconstructed indicator dump."""

    STRATEGY_ENTRY = "strategy_entry"
    DISCRETIONARY = "discretionary"
    TAKE_PROFIT = "take_profit"
    STOP = "stop"
    TIME_EXIT = "time_exit"
    BRACKET = "bracket"


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def require_utc(value: datetime) -> datetime:
    """Reject naive or non-UTC datetimes so why-trade records stay ordered in UTC."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be timezone-aware UTC")
    return value.astimezone(UTC)


class TradeReasonNote(_FrozenModel):
    """One attributed discretionary or review note. Not a fill-ledger rewrite."""

    origin: TradeReasonNoteOrigin
    body: str = Field(min_length=1, max_length=4000)
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Keep note times timezone-aware UTC."""
        return require_utc(value)


class TradeReasonNoteWrite(_FrozenModel):
    """Operator-authored note without server identity."""

    origin: TradeReasonNoteOrigin
    body: str = Field(min_length=1, max_length=4000)


class TradeReasonStrategy(_FrozenModel):
    """Published strategy identity frozen at intent persist. Absent for discretionary books."""

    strategy_id: UUID | None = None
    strategy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT)
    name: str | None = Field(default=None, max_length=120)
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_consistent_identity(self) -> Self:
        """Fingerprint, id, name, and version are all present or all absent."""
        present = (
            self.strategy_id is not None,
            self.strategy_fingerprint is not None,
            self.name is not None,
            self.version is not None,
        )
        if any(present) and not all(present):
            raise ValueError(
                "published strategy identity must include id, fingerprint, name, version"
            )
        return self


class TradeReasonSignal(_FrozenModel):
    """Closed-bar signal facts actually used at persist. Never interpolated candles."""

    kind: TradeReasonSignalKind
    last_signal: str | None = Field(default=None, max_length=32)
    candle_starts_at: datetime
    timeframe: str | None = None

    @field_validator("candle_starts_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Keep the decision bar timezone-aware UTC."""
        return require_utc(value)

    @field_validator("timeframe")
    @classmethod
    def require_venue_clock(cls, value: str | None) -> str | None:
        """Accept only ingested venue clocks when a timeframe is named."""
        if value is None:
            return None
        if value not in _TIMEFRAME:
            raise ValueError("timeframe must be an ingested venue clock")
        return value


class TradeReasonRisk(_FrozenModel):
    """Risk-registry verdict frozen at intent persist."""

    decision: Literal["allow", "deny"]
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    detail: str = Field(min_length=1, max_length=500)
    policy_fingerprint: str = Field(pattern=_FINGERPRINT)
    policy_source: Literal["compiled_default", "published"]


class TradeReasonFillFact(_FrozenModel):
    """One exact fill from the execution ledger. Not a reconstructed candle."""

    fill_id: UUID
    price: str = Field(min_length=1, max_length=64)
    quantity: str = Field(min_length=1, max_length=64)
    fee: str = Field(min_length=1, max_length=64)
    filled_at: datetime

    @field_validator("filled_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Keep fill times timezone-aware UTC."""
        return require_utc(value)


class TradeReasonReconcile(_FrozenModel):
    """Live order/fill facts joined from the execution ledger on read."""

    order_id: UUID | None = None
    order_status: str | None = Field(default=None, max_length=16)
    filled_quantity: str | None = Field(default=None, max_length=64)
    reject_reason: str | None = Field(default=None, max_length=500)
    unknown_timeout: bool = False
    ledger_available: bool = True
    fills: tuple[TradeReasonFillFact, ...] = ()


class TradeReasonRecord(_FrozenModel):
    """One durable why-trade review document per order intent."""

    schema_version: Literal["thytrader-trade-reason-v1"] = TRADE_REASON_SCHEMA_VERSION
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime
    origin: TradeReasonOrigin
    intent_id: UUID
    deployment_id: UUID
    deployment_kind: Literal["strategy", "discretionary"]
    mode: Literal["paper", "live"]
    product_id: str = Field(pattern=SPOT_PRODUCT_ID_PATTERN)
    purpose: Literal["entry", "take_profit", "stop", "time_exit", "bracket"]
    side: Literal["buy", "sell"]
    strategy: TradeReasonStrategy | None = None
    signal: TradeReasonSignal
    risk: TradeReasonRisk
    notes: tuple[TradeReasonNote, ...] = ()
    reconcile: TradeReasonReconcile = Field(default_factory=TradeReasonReconcile)

    @field_validator("created_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Keep record times timezone-aware UTC."""
        return require_utc(value)

    @model_validator(mode="after")
    def require_strategy_on_strategy_books(self) -> Self:
        """Strategy books freeze published identity; discretionary books omit it."""
        if self.deployment_kind == "strategy" and self.strategy is None:
            raise ValueError("strategy deployments require published strategy identity")
        if self.deployment_kind == "discretionary" and self.strategy is not None:
            raise ValueError("discretionary books cannot cite a published strategy")
        return self


class TradeReasonListResponse(_FrozenModel):
    """Newest-first why-trade listing."""

    schema_version: Literal["thytrader-trade-reason-v1"] = TRADE_REASON_SCHEMA_VERSION
    trade_reasons: tuple[TradeReasonRecord, ...]
