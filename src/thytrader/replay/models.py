"""Strict, language-neutral evidence contracts for offline execution replay."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from thytrader.decimal_text import canonical_decimal
from thytrader.market_data.quality import validate_candle_timestamp
from thytrader.replay.codec import validate_no_coercion, wire_value
from thytrader.replay.numeric import financial_text, validate_numeric_inputs
from thytrader.replay.steps import ReplayStep
from thytrader.replay.windows import validate_auxiliary_windows, validate_window
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint

DecimalText = Annotated[str, Field(strict=True, pattern=r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")]
Reason = Annotated[str, Field(strict=True, pattern=r"^[A-Z][A-Z0-9_]{1,79}$")]


class FrozenModel(BaseModel):
    """Forbid coercion, extra fields and mutation at the evidence boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SourceIdentity(FrozenModel):
    """Original engine/build facts; absent source identity is never inferred from a checkout."""

    execution_build: str | None
    research_build: str | None


class LedgerEvent(FrozenModel):
    """One normalized observed fill, with side resolved from its authoritative order."""

    fill_id: str = Field(min_length=1, max_length=128)
    side: Literal["buy", "sell"]
    price: DecimalText
    quantity: DecimalText
    fee: DecimalText
    filled_at: datetime

    @field_validator("price", "quantity", "fee")
    @classmethod
    def canonical_amount(cls, value: str) -> str:
        """Require positive price/quantity, nonnegative fees and exact canonical spelling."""
        amount = financial_text(value)
        if amount < 0 or canonical_decimal(amount) != value:
            raise ValueError("financial values must be nonnegative canonical decimal strings")
        return value

    @field_validator("filled_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        """Refuse naive or non-UTC evidence."""
        if value.utcoffset() != timedelta(0):
            raise ValueError("timestamps must be UTC")
        return value

    @model_validator(mode="after")
    def positive_geometry(self) -> Self:
        """Zero-sized or zero-priced executions are not financial evidence."""
        if Decimal(self.price) <= 0 or Decimal(self.quantity) <= 0:
            raise ValueError("fill price and quantity must be positive")
        return self


class LedgerEvidence(FrozenModel):
    """Cashflow ledger evidence, separate from allocation-funded performance capital."""

    starting_cash: DecimalText
    observed_ending_cash: DecimalText
    fills: tuple[LedgerEvent, ...] = Field(max_length=5000)

    @model_validator(mode="after")
    def ordered_unique_fills(self) -> Self:
        """A fill is applied once in recorded chronological order, never silently deduplicated."""
        ids = [item.fill_id for item in self.fills]
        instants = [item.filled_at for item in self.fills]
        if len(set(ids)) != len(ids) or instants != sorted(instants):
            raise ValueError("fill identities must be unique and timestamps ordered")
        for value in (self.starting_cash, self.observed_ending_cash):
            if canonical_decimal(financial_text(value)) != value:
                raise ValueError("cash must be canonical")
        return self


class ReplayPackage(FrozenModel):
    """Immutable input evidence; missing historical fields stay explicitly absent."""

    schema_version: Literal["thytrader-replay-v1"]
    evidence_kind: Literal["synthetic", "prospective", "historical"]
    source: SourceIdentity
    strategy: StrategyDefinition | None
    original_strategy: StrategyDefinition | None
    steps: tuple[ReplayStep, ...] = Field(default=(), max_length=1000)
    ledger: LedgerEvidence | None
    unavailable_reasons: tuple[Reason, ...]

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """Preflight numeric allocation while preserving Pydantic's strict JSON decoding.

        A before-model validator changes strict JSON tuple/enum decoding into Python
        decoding. Inspect separately, then pass the original bytes to the JSON engine.
        """
        validate_numeric_inputs(json.loads(json_data))
        return super().model_validate_json(
            json_data,
            strict=strict,
            extra=extra,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    @model_validator(mode="after")
    def validate_temporal_inputs(self) -> Self:
        """Reject future/gapped windows and timestamp identity contradictions before publication."""
        wire_value(self.model_dump(mode="python", by_alias=True, warnings="error"))
        if self.steps and self.strategy is None:
            raise ValueError("execution steps require an exact strategy snapshot")
        for step in self.steps:
            if self.strategy is None:
                raise ValueError("missing strategy")
            validate_step_inputs(step, self.strategy)
        return self


def validate_step_inputs(step: ReplayStep, strategy: StrategyDefinition) -> None:
    """Validate observed clock order and identity before consuming completed candles."""
    for instant in (step.data_available_at, step.evaluation_started_at, step.decision_completed_at):
        validate_candle_timestamp(instant)
    if not step.data_available_at <= step.evaluation_started_at <= step.decision_completed_at:
        raise ValueError("capture timestamps are out of causal order")
    validate_observation_times(step)
    mark_keys = [key for key, _amount in step.marks]
    if len(set(mark_keys)) != len(mark_keys):
        raise ValueError("mark product identities must be unique")
    if step.before.deployment.strategy_fingerprint != strategy_fingerprint(strategy):
        raise ValueError("book and strategy identities disagree")
    if (
        step.product.product_id != strategy.instrument.product_id
        or step.product.base_currency != strategy.instrument.base_currency
        or step.product.quote_currency != strategy.instrument.quote_currency
        or step.before.deployment.product_id != strategy.instrument.product_id
    ):
        raise ValueError("primary product identity disagrees with strategy or book")
    if step.before.deployment.timeframe not in {None, strategy.timeframe}:
        raise ValueError("book timeframe disagrees with strategy clock")
    if step.operation not in {"closed_bar", "reconcile"}:
        raise ValueError("unsupported execution step")
    if step.operation == "closed_bar" and not step.candles:
        raise ValueError("empty closed-bar execution step")
    validate_primary_window(step, strategy)
    validate_auxiliary_windows(step, strategy)


def validate_observation_times(step: ReplayStep) -> None:
    """Bound invocation/return spans and nondecreasing actual clock reads, not history."""
    start, done = step.evaluation_started_at, step.decision_completed_at
    for event in step.broker_events:
        validate_candle_timestamp(event.started_at)
        validate_candle_timestamp(event.completed_at)
        if not start <= event.started_at <= event.completed_at <= done:
            raise ValueError("broker observation is outside the operation envelope")
    previous = start
    for event in step.runtime_inputs:
        if event.kind == "clock":
            instant = datetime.fromisoformat(event.value)
            validate_candle_timestamp(instant)
            if not previous <= instant <= done:
                raise ValueError("runtime clock is outside the causal operation envelope")
            previous = instant


def validate_primary_window(step: ReplayStep, strategy: StrategyDefinition) -> None:
    """Selected primary bars must be complete, contiguous and semantically valid as observed."""
    validate_window(step.candles, strategy.timeframe, step.data_available_at)


def canonical_bytes(package: ReplayPackage) -> bytes:
    """Revalidate before assigning compact sorted UTF-8 JSON identity."""
    original = wire_value(package.model_dump(mode="python", by_alias=True, warnings="error"))
    checked = ReplayPackage.model_validate_json(json.dumps(original))
    normalized = wire_value(checked.model_dump(mode="python", by_alias=True))
    validate_no_coercion(original, normalized)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def fingerprint(content: bytes) -> str:
    """Name exact canonical evidence bytes with SHA-256."""
    return "sha256:" + sha256(content).hexdigest()
