"""Durable per-bar decision records for paper and live strategy bots (ADR 0087).

One ``BarDecision`` explains what a bot decided on one completed bar of one covered
product and why: the outcome, the action taken, the entry-rule tree with the exact
values each leaf read (reusing the research ``SignalTraceRecord``), the risk verdict,
linked orders and fills, the close price, and the position at the end of the bar.
Records are explanations only; they never feed back into trading.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, TypeAlias
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, ConfigDict, Field, field_validator

from thytrader.execution.models import (  # noqa: TC001 - Pydantic field types.
    DeploymentMode,
    IntentPurpose,
    OrderKind,
    OrderSide,
    OrderStatus,
    PositionSide,
)
from thytrader.market_data.models import DatasetTimeframe  # noqa: TC001 - Pydantic field type.
from thytrader.market_data.products import SPOT_PRODUCT_ID_PATTERN
from thytrader.research.trace import (  # noqa: TC001 - Pydantic field types.
    EntryConditionOutcome,
    SignalTraceRecord,
)
from thytrader.strategies.models import ComparisonOperator  # noqa: TC001 - Pydantic field type.

DECISION_SCHEMA_VERSION: Literal["thytrader-bar-decision-v1"] = "thytrader-bar-decision-v1"
DECISION_RETENTION_MAX_ROWS_PER_DEPLOYMENT = 20_000
DECISION_RETENTION_MAX_AGE = timedelta(days=180)
DECISION_PAGE_MAX_LIMIT = 200
_FINGERPRINT = r"^sha256:[0-9a-f]{64}$"
_REASON_CODE = r"^[A-Z][A-Z0-9_]{0,63}$"
_MAX_LINKED = 50

DecisionDecimal = Annotated[
    str,
    Field(pattern=r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$", max_length=6210),
]


class DecisionOutcome(StrEnum):
    """What one completed bar amounted to for one covered product."""

    ENTRY_SIGNAL = "entry_signal"
    NO_SIGNAL = "no_signal"
    HOLDING = "holding"
    EXIT = "exit"
    ENTRY_BLOCKED = "entry_blocked"
    SKIPPED = "skipped"
    ERROR = "error"


class DecisionAction(StrEnum):
    """What the bot did with orders on this bar."""

    NONE = "none"
    INTENT_CREATED = "intent_created"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_CANCELED = "order_canceled"
    REPRICED = "repriced"


class DecisionSkipReason(StrEnum):
    """Why the entry rule was not evaluated (or could not decide) on this bar."""

    COOLDOWN = "cooldown"
    MAX_OPEN_POSITIONS = "max_open_positions"
    WARMUP = "warmup"
    PENDING_ENTRY = "pending_entry"
    PAUSED = "paused"
    STOPPED = "stopped"
    DATA_GAP = "data_gap"
    USER_FEED_GATE = "user_feed_gate"
    CATCH_UP = "catch_up"
    ENTRIES_DISABLED = "entries_disabled"


class DecisionExitReason(StrEnum):
    """Which exit rule closed (or is closing) the position on this bar."""

    STOP = "stop"
    TRAIL = "trail"
    TARGET = "target"
    TIME = "time"
    FLATTEN = "flatten"


class ConditionResult(StrEnum):
    """Tri-state result of one rule node: undefined inputs are unknown, never false."""

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class _FrozenDecisionModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_utc(value: datetime) -> datetime:
    """Keep journal timestamps timezone-aware UTC."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("decision timestamps must be timezone-aware UTC")
    return value.astimezone(UTC)


class DecisionOperand(_FrozenDecisionModel):
    """One comparison side: an indicator series or an exact literal, with its value."""

    kind: Literal["indicator", "literal"]
    label: str = Field(min_length=1, max_length=120)
    key: str | None = Field(default=None, max_length=96)
    value: DecisionDecimal | None = None
    previous_value: DecisionDecimal | None = None


class ConditionComparisonTrace(_FrozenDecisionModel):
    """One leaf of the rule tree with both operand values and its tri-state result."""

    node: Literal["comparison"] = "comparison"
    result: ConditionResult
    label: str = Field(min_length=1, max_length=300)
    operator: ComparisonOperator
    operator_symbol: str = Field(min_length=1, max_length=16)
    left: DecisionOperand
    right: DecisionOperand


class ConditionGroupTrace(_FrozenDecisionModel):
    """ALL / ANY / NOT grouping with the group's own tri-state result."""

    node: Literal["all", "any", "not"]
    result: ConditionResult
    children: tuple[ConditionTrace, ...] = Field(min_length=1, max_length=20)


ConditionTrace: TypeAlias = Annotated[  # noqa: UP040 - recursive alias with a discriminator.
    ConditionComparisonTrace | ConditionGroupTrace,
    Field(discriminator="node"),
]
ConditionGroupTrace.model_rebuild()


class HtfFilterTrace(_FrozenDecisionModel):
    """The declared higher-timeframe filter, evaluated on its last completed bar."""

    timeframe: DatasetTimeframe
    outcome: EntryConditionOutcome
    condition: ConditionTrace


class EntryRuleTrace(_FrozenDecisionModel):
    """The evaluated entry rule: combined outcome, rule tree, HTF filter, and values.

    ``signal`` is the same ``SignalTraceRecord`` research emits per candle, so paper,
    live, and backtest evidence share one shape for indicator values on a bar.
    """

    outcome: EntryConditionOutcome
    entry: ConditionTrace
    htf_filter: HtfFilterTrace | None = None
    signal: SignalTraceRecord | None = None


class DecisionRisk(_FrozenDecisionModel):
    """The risk-registry or freshness verdict that admitted or refused an entry."""

    decision: Literal["allow", "deny"]
    reason_code: str = Field(pattern=_REASON_CODE)
    detail: str = Field(min_length=1, max_length=500)


class DecisionPosition(_FrozenDecisionModel):
    """The product book at the end of the bar."""

    side: PositionSide
    quantity: DecisionDecimal
    entry_price: DecisionDecimal
    stop_price: DecisionDecimal
    target_price: DecisionDecimal


class DecisionOrder(_FrozenDecisionModel):
    """One order created, changed, or filled within this bar's decision window."""

    order_id: UUID
    intent_id: UUID
    purpose: IntentPurpose | None = None
    side: OrderSide
    kind: OrderKind
    status: OrderStatus
    quantity: DecisionDecimal
    price: DecisionDecimal | None = None
    filled_quantity: DecisionDecimal
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_utc_created(cls, value: datetime) -> datetime:
        """Keep order timestamps UTC."""
        return _require_utc(value)


class DecisionFill(_FrozenDecisionModel):
    """One exact fill whose economics were applied within this bar's decision window."""

    fill_id: UUID
    order_id: UUID
    purpose: IntentPurpose | None = None
    side: OrderSide | None = None
    price: DecisionDecimal
    quantity: DecisionDecimal
    fee: DecisionDecimal
    filled_at: datetime

    @field_validator("filled_at")
    @classmethod
    def require_utc_filled(cls, value: datetime) -> datetime:
        """Keep fill timestamps UTC."""
        return _require_utc(value)


class BarDecision(_FrozenDecisionModel):
    """What one paper/live strategy bot decided on one completed bar, and why.

    Identity is ``(deployment_id, product_id, bar_starts_at)``; writes are upserts, so
    a restart replay rewrites the same row instead of duplicating it.
    """

    schema_version: Literal["thytrader-bar-decision-v1"] = DECISION_SCHEMA_VERSION
    deployment_id: UUID
    strategy_id: UUID | None = None
    strategy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT)
    product_id: str = Field(pattern=SPOT_PRODUCT_ID_PATTERN)
    timeframe: DatasetTimeframe
    mode: DeploymentMode
    bar_starts_at: datetime
    bar_closes_at: datetime
    evaluated_at: datetime
    outcome: DecisionOutcome
    reason_code: str = Field(pattern=_REASON_CODE)
    summary: str = Field(min_length=1, max_length=500)
    skip_reason: DecisionSkipReason | None = None
    exit_reason: DecisionExitReason | None = None
    action: DecisionAction = DecisionAction.NONE
    intent_id: UUID | None = None
    order_ids: tuple[UUID, ...] = Field(default=(), max_length=_MAX_LINKED)
    orders: tuple[DecisionOrder, ...] = Field(default=(), max_length=_MAX_LINKED)
    fills: tuple[DecisionFill, ...] = Field(default=(), max_length=_MAX_LINKED)
    close_price: DecisionDecimal | None = None
    rule: EntryRuleTrace | None = None
    risk: DecisionRisk | None = None
    position: DecisionPosition | None = None

    @field_validator("bar_starts_at", "bar_closes_at", "evaluated_at")
    @classmethod
    def require_utc_timestamps(cls, value: datetime) -> datetime:
        """Keep bar and evaluation timestamps UTC."""
        return _require_utc(value)


class DecisionPage(_FrozenDecisionModel):
    """One newest-first page of decisions plus an opaque cursor for the next page."""

    decisions: tuple[BarDecision, ...]
    next_cursor: str | None = None
