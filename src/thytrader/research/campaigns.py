"""Frozen, research-only campaigns and prospective validation evidence (ADR 0109)."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from thytrader.backtest.projections import BacktestProjection
from thytrader.backtest.submission import BacktestStartRequest
from thytrader.trading.economics import EconomicDecimal


class CampaignModel(BaseModel):
    """Bound every persisted and HTTP campaign shape."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ValidationGates(CampaignModel):
    """Frozen sample, return, and drawdown requirements; never deployment permission."""

    minimum_trades: int = Field(default=10, strict=True, ge=1, le=100_000)
    minimum_net_return_fraction: EconomicDecimal = "0"
    maximum_drawdown_fraction: EconomicDecimal = "0.15"

    @field_validator("minimum_net_return_fraction", "maximum_drawdown_fraction")
    @classmethod
    def bound_fraction(cls, value: str) -> str:
        """Reject fractions above one at campaign creation."""
        if Decimal(value) > 1:
            raise ValueError("gate fractions must be at most 1")
        return value


class CampaignCaseStart(CampaignModel):
    """One named strategy, market, window, and cost/stress scenario to freeze."""

    key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    request: BacktestStartRequest
    strategy_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$", exclude_if=lambda value: value is None
    )


class CampaignStart(CampaignModel):
    """Confirm once to freeze and authorize research jobs when their data is ready."""

    name: str = Field(min_length=1, max_length=160)
    kind: Literal["historical", "prospective"]
    cases: tuple[CampaignCaseStart, ...] = Field(min_length=1, max_length=2000)
    gates: ValidationGates = Field(default_factory=ValidationGates)
    deadline: datetime

    @field_validator("deadline")
    @classmethod
    def utc_deadline(cls, value: datetime) -> datetime:
        """Use explicit UTC deadlines for worker scheduling."""
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("deadline must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def unique_explicit_windows(self) -> Self:
        """Freeze explicit aligned windows rather than moving latest-data windows."""
        if len({case.key for case in self.cases}) != len(self.cases):
            raise ValueError("campaign case keys must be unique")
        for case in self.cases:
            start, end = case.request.evaluation_start, case.request.evaluation_end
            if start is None or end is None or start >= end:
                raise ValueError("campaign cases require explicit increasing evaluation bounds")
            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError("campaign evaluation bounds must be timezone-aware")
            if end > self.deadline:
                raise ValueError("deadline must cover every evaluation window")
        return self


class FrozenCampaignCase(CampaignCaseStart):
    """The exact snapshotted rules used even when the mutable strategy changes."""

    strategy_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class CampaignManifest(CampaignModel):
    """Immutable campaign intent, with independently mutable job/validation state."""

    campaign_id: UUID
    name: str
    kind: Literal["historical", "prospective"]
    provider: str
    frozen_at: datetime
    deadline: datetime
    gates: ValidationGates
    cases: tuple[FrozenCampaignCase, ...]

    def fingerprint(self) -> str:
        """Content-address the complete frozen manifest including case order."""
        return "sha256:" + sha256(self.model_dump_json().encode()).hexdigest()


class CampaignCaseStatus(StrEnum):
    """Research lifecycle; passing is evidence and grants no trading authority."""

    WAITING_FOR_DATA = "waiting_for_data"
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED_GATE = "failed_gate"
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    FAILED = "failed"
    EXPIRED = "expired"


TERMINAL_CAMPAIGN_CASES = frozenset(
    {
        CampaignCaseStatus.PASSED,
        CampaignCaseStatus.FAILED_GATE,
        CampaignCaseStatus.INSUFFICIENT_SAMPLE,
        CampaignCaseStatus.FAILED,
        CampaignCaseStatus.EXPIRED,
    }
)


class CampaignCaseState(CampaignModel):
    """Durable trace from one frozen case to its child job and bounded result."""

    key: str
    status: CampaignCaseStatus = CampaignCaseStatus.WAITING_FOR_DATA
    job_id: UUID | None = None
    result: BacktestProjection | None = None
    detail: str = Field(default="Waiting for verified complete data.", max_length=500)


class CampaignRecord(CampaignModel):
    """One verified manifest plus evolving evidence, read after process restarts."""

    manifest: CampaignManifest
    manifest_fingerprint: str
    updated_at: datetime
    cases: tuple[CampaignCaseState, ...]

    @model_validator(mode="after")
    def verify_manifest(self) -> Self:
        """Reject altered frozen intent and states belonging to a different manifest."""
        if self.manifest_fingerprint != self.manifest.fingerprint():
            raise ValueError("campaign manifest fingerprint mismatch")
        if tuple(case.key for case in self.cases) != tuple(
            case.key for case in self.manifest.cases
        ):
            raise ValueError("campaign state keys do not match frozen cases")
        return self

    @property
    def completed(self) -> bool:
        """Whether every child has a terminal research outcome."""
        return all(case.status in TERMINAL_CAMPAIGN_CASES for case in self.cases)


def validation_status(result: BacktestProjection, gates: ValidationGates) -> CampaignCaseStatus:
    """Require enough closed trades before interpreting profitability or drawdown."""
    summary = result.summary
    if summary.trade_count < gates.minimum_trades:
        return CampaignCaseStatus.INSUFFICIENT_SAMPLE
    if Decimal(summary.total_return_fraction) <= Decimal(
        gates.minimum_net_return_fraction
    ) or Decimal(summary.maximum_drawdown_fraction) > Decimal(gates.maximum_drawdown_fraction):
        return CampaignCaseStatus.FAILED_GATE
    return CampaignCaseStatus.PASSED
