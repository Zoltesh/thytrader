"""Desk, decision, wake and book models (ADR 0147).

A desk is a paper trading mandate an external agent harness wakes on a cadence. The model's
answer is one :class:`DeskDecision`; ThyTrader validates it against the desk's caps, applies it
through discretionary paper books and journals the wake whether or not anything traded.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DESK_CONTRACT = "thytrader-agent-desk-v1"
MAX_THESIS_CHARS = 4000
MAX_RESPONSE_CHARS = 20_000
MAX_MANDATE_CHARS = 4000
MAX_PRODUCTS = 20


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DeskStatus(StrEnum):
    """Whether wakes may act."""

    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class DeskAction(StrEnum):
    """What one wake decided."""

    NO_TRADE = "no_trade"
    OPEN = "open"
    CLOSE = "close"
    INVALID = "invalid"


class WakeOutcome(StrEnum):
    """What ThyTrader did with the decision."""

    APPLIED = "applied"
    REFUSED = "refused"
    INVALID = "invalid"
    ERROR = "error"


class DeskCaps(_Frozen):
    """Hard limits ThyTrader enforces on every decision, whatever the model says."""

    max_open_positions: int = Field(default=3, ge=1, le=20)
    max_position_fraction: Decimal = Field(default=Decimal("0.25"), gt=0, le=1)
    max_entries_per_day: int = Field(default=8, ge=1, le=100)
    daily_loss_fraction: Decimal = Field(default=Decimal("0.05"), gt=0, le=1)


class PendingOpen(_Frozen):
    """Pool cash reserved for one open whose placement has not been linked yet.

    Set in the same write that debits the pool, before the order is placed, and cleared in the
    write that links the placed book. One left behind by a crash is resolved on the next read:
    the book found under ``idempotency_key`` is linked, otherwise the cash is refunded.
    """

    wake_id: str
    idempotency_key: str
    product_id: str
    side: Literal["long", "short"]
    allocated_quote: Decimal = Field(gt=0)
    reserved_at: datetime
    thesis: str = Field(default="", max_length=MAX_THESIS_CHARS)


class Desk(_Frozen):
    """One desk: its pool, universe, cadence, harness and caps."""

    desk_id: str
    name: str = Field(min_length=1, max_length=120)
    mode: Literal["paper", "live"] = "paper"
    live_acknowledged_at: datetime | None = None
    quote_currency: str = Field(pattern=r"^(USD|USDC|USDT)$")
    starting_cash: Decimal = Field(gt=0)
    cash: Decimal = Field(ge=0)
    products: tuple[str, ...] = Field(min_length=1, max_length=MAX_PRODUCTS)
    timeframe: str = "5m"
    cadence_minutes: int = Field(default=30, ge=5, le=1440)
    harness: str = Field(default="claude", pattern=r"^[a-z][a-z0-9_-]{1,31}$")
    model: str | None = Field(default=None, max_length=80)
    mandate: str = Field(default="", max_length=MAX_MANDATE_CHARS)
    caps: DeskCaps = DeskCaps()
    status: DeskStatus = DeskStatus.ACTIVE
    day_open_equity: Decimal | None = None
    day_open_at: datetime | None = None
    pending_open: PendingOpen | None = None
    revision: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("products")
    @classmethod
    def _distinct_products(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Desk products must be distinct.")
        return value


class DeskBook(_Frozen):
    """One trade: a discretionary paper book the desk funded and later settles."""

    desk_id: str
    deployment_id: str
    product_id: str
    side: Literal["long", "short"]
    allocated_quote: Decimal = Field(gt=0)
    opened_at: datetime
    wake_id: str
    settled_at: datetime | None = None
    settled_cash: Decimal | None = None


class DeskDecision(_Frozen):
    """The one decision a model returns per wake (``DESK_CONTRACT``)."""

    action: Literal["no_trade", "open", "close"]
    thesis: str = Field(min_length=1, max_length=MAX_THESIS_CHARS)
    product_id: str | None = None
    side: Literal["long", "short"] | None = None
    size_fraction: Decimal | None = Field(default=None, gt=0, le=1)
    entry: Literal["market", "limit"] = "market"
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    take_profit_price: Decimal | None = Field(default=None, gt=0)
    deployment_id: str | None = None

    @model_validator(mode="after")
    def _fields_for_action(self) -> DeskDecision:
        if self.action == "open":
            missing = [
                name
                for name in (
                    "product_id",
                    "side",
                    "size_fraction",
                    "stop_price",
                    "take_profit_price",
                )
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(f"An open decision needs {', '.join(missing)}.")
            if self.entry == "limit" and self.limit_price is None:
                raise ValueError("A limit entry needs limit_price.")
        if self.action == "close" and self.deployment_id is None:
            raise ValueError("A close decision needs deployment_id (the desk book to close).")
        return self


class DeskWake(_Frozen):
    """One journaled wake: what the model said and what ThyTrader did."""

    wake_id: str
    desk_id: str
    started_at: datetime
    finished_at: datetime
    harness: str
    model: str | None = None
    briefing_fingerprint: str
    raw_response: str = Field(max_length=MAX_RESPONSE_CHARS)
    action: DeskAction
    outcome: WakeOutcome
    outcome_code: str | None = None
    detail: str = ""
    thesis: str = ""
    decision: dict[str, object] | None = None
    equity_quote: Decimal | None = None
    deployment_id: str | None = None
