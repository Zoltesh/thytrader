"""Typed execution-quality and twin-comparison report documents (ADR 0116).

Evidence-reason vocabularies, canonical decimal/fingerprint field types, the frozen
pydantic report models (each binds its own content fingerprint), and the journaled
decision-close evidence the report builder consumes. The fill fold and report builders
live in :mod:`thytrader.execution.execution_quality`, which re-exports every public
name here; this module never imports it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_serializer, model_validator

from thytrader.execution.models import DeploymentMode, OrderSide
from thytrader.research.indicators import canonical_decimal

EXECUTION_QUALITY_SCHEMA_VERSION: Literal["thytrader-execution-quality-v1"] = (
    "thytrader-execution-quality-v1"
)
EXECUTION_TWIN_SCHEMA_VERSION: Literal["thytrader-execution-twin-comparison-v1"] = (
    "thytrader-execution-twin-comparison-v1"
)
_PLACEHOLDER_FINGERPRINT = "sha256:" + "0" * 64
_PLAIN_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")

MakerTakerEvidence = Literal["maker", "taker"]
FingerprintText = Annotated[str, Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]
CycleDirection = Literal["long", "short"]


def _exact_decimal_text(value: str) -> str:
    """Require canonical plain decimal text so sums stay byte-comparable."""
    if _PLAIN_DECIMAL.fullmatch(value) is None:
        raise ValueError("execution-quality amounts must be canonical plain decimal strings")
    parsed = Decimal(value)
    if not parsed.is_finite() or canonical_decimal(parsed) != value:
        raise ValueError("execution-quality amounts must be canonical finite decimal strings")
    return value


QualityDecimalText = Annotated[
    str, Field(strict=True, max_length=12500), AfterValidator(_exact_decimal_text)
]


def _utc_text(value: datetime) -> str:
    """Render one UTC instant with the canonical Z suffix."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ExecutionQualityEvidenceReason(StrEnum):
    """Why one report's evidence is incomplete; absent evidence is never zero."""

    UNAPPLIED_FILL_ECONOMICS = "unapplied_fill_economics"
    FILL_WITHOUT_ORDER = "fill_without_order"
    FILL_WITHOUT_JOURNALED_CLOSE = "fill_without_journaled_close"
    FILL_WITHOUT_INTENT = "fill_without_intent"
    NON_CAUSAL_DECISION_REFERENCE = "non_causal_decision_reference"
    LIQUIDITY_NOT_RECORDED = "liquidity_not_recorded"
    DECISION_JOURNAL_UNAVAILABLE = "decision_journal_unavailable"
    DECISION_COVERAGE_LIMITED = "decision_coverage_limited"
    TIMEFRAME_UNKNOWN = "timeframe_unknown"
    OPEN_CYCLE_PRESENT = "open_cycle_present"
    OPEN_POSITION_MISMATCH = "open_position_mismatch"
    OPEN_CYCLE_WITHOUT_POSITION_ROW = "open_cycle_without_position_row"
    POSITION_WITHOUT_FILL_EVIDENCE = "position_without_fill_evidence"
    POSITION_FLIP_FILL = "position_flip_fill"
    LEDGER_REALIZATION_DELTA = "ledger_realization_delta"


class TwinComparisonReason(StrEnum):
    """Why two explicitly linked books cannot be compared on execution evidence."""

    NO_OVERLAPPING_FILLS = "no_overlapping_fills"
    INCOMPLETE_PAPER_EVIDENCE = "incomplete_paper_evidence"
    INCOMPLETE_LIVE_EVIDENCE = "incomplete_live_evidence"
    SNAPSHOT_FINGERPRINTS_DIFFER = "snapshot_fingerprints_differ"
    TRADING_RULES_UNVERIFIED = "trading_rules_unverified"
    TRADING_RULES_INCOMPATIBLE = "trading_rules_incompatible"
    LIFETIME_WINDOWS_DIFFER = "lifetime_windows_differ"
    FILL_POPULATIONS_DIFFER = "fill_populations_differ"
    ORDER_POPULATIONS_DIFFER = "order_populations_differ"
    ORDER_POPULATION_UNVERIFIED = "order_population_unverified"
    FILL_POPULATION_UNVERIFIED = "fill_population_unverified"
    LIVE_FILL_COVERAGE_INCOMPLETE = "live_fill_coverage_incomplete"
    ENTRY_FILL_COUNT_DIVERGENCE = "entry_fill_count_divergence"
    ENTRY_SIGNAL_COUNT_DIVERGENCE = "entry_signal_count_divergence"
    UNFILLED_ENTRY_ORDER_DIVERGENCE = "unfilled_entry_order_divergence"
    PAPER_FEE_RATES_DEFAULTED = "paper_fee_rates_defaulted"
    LIVE_FILLS_WITHOUT_LIQUIDITY_EVIDENCE = "live_fills_without_liquidity_evidence"


class _FrozenQualityModel(BaseModel):
    """Reject unknown fields and prevent mutation of execution evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExecutionQualityFill(_FrozenQualityModel):
    """One recorded fill with the liquidity and slippage evidence the records prove.

    ``liquidity`` is ``maker`` only for recorded post-only fills and ``taker`` only for
    recorded marketable fills; venue-decided bracket and stop-limit fills report null
    because the venue did not record which side they took. ``slippage_bps`` is signed
    against the persisted intent's completed decision-bar close (positive is worse).
    The bar must have closed by intent creation, order creation, and fill time. This
    is an original-intent benchmark, not a contemporaneous quote at a later reprice.
    Missing or noncausal references are null, never replaced with a fill-bar close.
    """

    fill_id: UUID
    order_id: UUID
    side: OrderSide
    price: QualityDecimalText
    quantity: QualityDecimalText
    fee: QualityDecimalText
    filled_at: datetime
    liquidity: MakerTakerEvidence | None = None
    slippage_bps: QualityDecimalText | None = None
    reference_price: QualityDecimalText | None = None
    reference_intent_id: UUID | None = None
    reference_bar_starts_at: datetime | None = None
    reference_bar_closes_at: datetime | None = None

    @field_serializer("reference_bar_starts_at", "reference_bar_closes_at", when_used="json")
    def serialize_reference_time(self, value: datetime | None) -> str | None:
        """Render causal benchmark provenance without synthesizing missing instants."""
        return None if value is None else _utc_text(value)

    @field_serializer("filled_at", when_used="json")
    def serialize_filled_at(self, value: datetime) -> str:
        """Render the fill instant canonically."""
        return _utc_text(value)


class ExecutionQualityRoundTrip(_FrozenQualityModel):
    """One fully closed position cycle with exact recorded fee and price evidence.

    Sums are direct: ``fill_price_pnl_before_fees`` uses only recorded fill prices and
    quantities, fees are the exact recorded fill fees, and ``net_pnl`` is their
    difference. Partial exits and pyramiding adds appear as multiple entry or exit
    fills. A fill that over-covers the cycle is clamped to the remaining quantity while
    its full recorded fee stays on the exit, and the report flags ``position_flip_fill``.
    """

    direction: CycleDirection
    opened_at: datetime
    closed_at: datetime
    closed_quantity: QualityDecimalText
    entries: tuple[ExecutionQualityFill, ...] = Field(min_length=1)
    exits: tuple[ExecutionQualityFill, ...] = Field(min_length=1)
    fill_price_pnl_before_fees: QualityDecimalText
    entry_fees: QualityDecimalText
    exit_fees: QualityDecimalText
    net_pnl: QualityDecimalText
    slippage_bps: QualityDecimalText | None = None
    slippage_fills_journaled: int = Field(ge=0)
    slippage_fills_total: int = Field(ge=1)

    @field_serializer("opened_at", "closed_at", when_used="json")
    def serialize_trip_timestamps(self, value: datetime) -> str:
        """Render trip boundaries canonically."""
        return _utc_text(value)


class ExecutionQualityOpenCycle(_FrozenQualityModel):
    """A still-open cycle including partial exits, without invented closed-trade PnL."""

    direction: CycleDirection
    opened_at: datetime
    quantity: QualityDecimalText
    entry_notional: QualityDecimalText
    entry_fees: QualityDecimalText
    entries: tuple[ExecutionQualityFill, ...] = Field(min_length=1)
    position_matches_ledger: bool
    exits: tuple[ExecutionQualityFill, ...] = ()

    @field_serializer("opened_at", when_used="json")
    def serialize_opened_at(self, value: datetime) -> str:
        """Render the opening instant canonically."""
        return _utc_text(value)


class ExecutionQualityBook(_FrozenQualityModel):
    """One product's closed round trips, open cycle, and exact closed-trade totals."""

    product_id: str
    closed_trade_count: int = Field(ge=0)
    round_trips: tuple[ExecutionQualityRoundTrip, ...] = ()
    recorded_fills: tuple[ExecutionQualityFill, ...] = ()
    open_cycle: ExecutionQualityOpenCycle | None = None
    fill_price_pnl_before_fees: QualityDecimalText
    entry_fees: QualityDecimalText
    exit_fees: QualityDecimalText
    net_pnl: QualityDecimalText


class JournaledCloseCoverage(_FrozenQualityModel):
    """The bar window the fetched decision rows actually cover."""

    oldest_bar: datetime
    newest_bar: datetime
    bars: int = Field(ge=1)

    @field_serializer("oldest_bar", "newest_bar", when_used="json")
    def serialize_coverage(self, value: datetime) -> str:
        """Render coverage boundaries canonically."""
        return _utc_text(value)


class ExecutionQualityTotals(_FrozenQualityModel):
    """Deployment-wide sums over closed round trips, plus disclosure deltas."""

    closed_trade_count: int = Field(ge=0)
    open_cycle_count: int = Field(ge=0)
    applied_fill_count: int = Field(ge=0)
    fill_price_pnl_before_fees: QualityDecimalText
    entry_fees: QualityDecimalText
    exit_fees: QualityDecimalText
    net_pnl: QualityDecimalText
    ledger_realized_delta: QualityDecimalText | None = None
    weighted_slippage_bps: QualityDecimalText | None = None
    slippage_fills_journaled: int = Field(ge=0)
    slippage_fills_total: int = Field(ge=0)
    unfilled_entry_orders: int = Field(ge=0)
    journaled_entry_signals: int | None = None
    first_fill_at: datetime | None = None
    last_fill_at: datetime | None = None

    @field_serializer("first_fill_at", "last_fill_at", when_used="json")
    def serialize_fill_bounds(self, value: datetime | None) -> str | None:
        """Render fill bounds canonically."""
        return None if value is None else _utc_text(value)


class ExecutionQualityEvidence(_FrozenQualityModel):
    """Whether every recorded fact could be proven, and which could not."""

    complete: bool
    reasons: tuple[ExecutionQualityEvidenceReason, ...] = ()
    journaled_close_coverage: JournaledCloseCoverage | None = None
    unapplied_fill_count: int = Field(ge=0)
    orphan_fill_count: int = Field(ge=0)


class ExecutionQualityReport(_FrozenQualityModel):
    """Read-only execution-quality evidence for one paper or live book."""

    schema_version: Literal["thytrader-execution-quality-v1"] = EXECUTION_QUALITY_SCHEMA_VERSION
    report_fingerprint: FingerprintText = _PLACEHOLDER_FINGERPRINT
    deployment_id: UUID
    product_id: str
    mode: DeploymentMode
    status: str
    strategy_fingerprint: str | None
    timeframe: str | None
    books: tuple[ExecutionQualityBook, ...] = ()
    totals: ExecutionQualityTotals
    evidence: ExecutionQualityEvidence

    @model_validator(mode="after")
    def require_report_identity(self) -> Self:
        """Bind the report to its own content identity, exactly like cost attribution."""
        payload = json.dumps(
            self.model_dump(mode="json", exclude={"report_fingerprint"}),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        expected = f"sha256:{sha256(payload).hexdigest()}"
        if self.report_fingerprint == _PLACEHOLDER_FINGERPRINT:
            object.__setattr__(self, "report_fingerprint", expected)
        elif self.report_fingerprint != expected:
            raise ValueError("execution-quality fingerprint does not match its evidence")
        return self


@dataclass(frozen=True, slots=True)
class JournaledDecisionClose:
    """A journaled bar close with its recorded completion instant."""

    price: Decimal
    bar_closes_at: datetime


@dataclass(frozen=True, slots=True)
class JournaledCloseEvidence:
    """Journaled closes plus the bounded window the fetched rows covered."""

    closes: dict[tuple[str, datetime], JournaledDecisionClose]
    coverage: tuple[datetime, datetime] | None
    rows_fetched: int
    coverage_limited: bool


class ExecutionTwinSide(_FrozenQualityModel):
    """One twin's closed-trade execution summary inside the shared comparison."""

    deployment_id: UUID
    mode: DeploymentMode
    status: str
    evidence_complete: bool
    closed_trade_count: int = Field(ge=0)
    entry_fill_count: int = Field(ge=0)
    applied_fill_count: int = Field(ge=0)
    unfilled_entry_orders: int = Field(ge=0)
    journaled_entry_signals: int | None = None
    fill_price_pnl_before_fees: QualityDecimalText
    entry_fees: QualityDecimalText
    exit_fees: QualityDecimalText
    net_pnl: QualityDecimalText
    weighted_slippage_bps: QualityDecimalText | None = None
    first_fill_at: datetime | None = None
    last_fill_at: datetime | None = None

    @field_serializer("first_fill_at", "last_fill_at", when_used="json")
    def serialize_side_bounds(self, value: datetime | None) -> str | None:
        """Render side fill bounds canonically."""
        return None if value is None else _utc_text(value)


class ExecutionFeeNormalization(_FrozenQualityModel):
    """Counterfactual fee normalization; realized amounts are never rewritten.

    Both amounts describe ALL applied lifetime live fills exactly once, independently
    of twin overlap. Unknown liquidity or excluded unapplied/orphan fills make the
    counterfactual and delta null, never a partial-subset number or an implied zero.
    """

    maker_fee_rate: QualityDecimalText
    taker_fee_rate: QualityDecimalText
    rate_source: Literal["stored_paper_assumptions", "documented_defaults"]
    observed_live_fees: QualityDecimalText
    counterfactual_live_fees_at_paper_rates: QualityDecimalText | None
    fee_delta: QualityDecimalText | None
    fills_without_liquidity_evidence: int = Field(ge=0)
    population: Literal["live_applied_fill_lifetime"] = "live_applied_fill_lifetime"
    fill_count: int = Field(ge=0)
    complete: bool


class TwinOverlapWindow(_FrozenQualityModel):
    """The shared fill-time window both books have recorded fills inside."""

    first_fill_at: datetime
    last_fill_at: datetime

    @field_serializer("first_fill_at", "last_fill_at", when_used="json")
    def serialize_overlap(self, value: datetime) -> str:
        """Render overlap bounds canonically."""
        return _utc_text(value)


class ExecutionTwinComparison(_FrozenQualityModel):
    """Read-only execution comparison of two explicitly linked paper/live books."""

    schema_version: Literal["thytrader-execution-twin-comparison-v1"] = (
        EXECUTION_TWIN_SCHEMA_VERSION
    )
    report_fingerprint: FingerprintText = _PLACEHOLDER_FINGERPRINT
    paper: ExecutionTwinSide
    live: ExecutionTwinSide
    strategy_fingerprint_paper: str | None
    strategy_fingerprint_live: str | None
    product_id: str
    timeframe: str | None
    overlap: TwinOverlapWindow | None = None
    population: Literal["recorded_fill_lifetime"] = "recorded_fill_lifetime"
    summaries_context_only: bool
    comparable: bool
    reasons: tuple[TwinComparisonReason, ...] = ()
    fee_normalization: ExecutionFeeNormalization | None = None

    @model_validator(mode="after")
    def require_comparison_identity(self) -> Self:
        """Bind the comparison to its own content identity."""
        payload = json.dumps(
            self.model_dump(mode="json", exclude={"report_fingerprint"}),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        expected = f"sha256:{sha256(payload).hexdigest()}"
        if self.report_fingerprint == _PLACEHOLDER_FINGERPRINT:
            object.__setattr__(self, "report_fingerprint", expected)
        elif self.report_fingerprint != expected:
            raise ValueError("execution-twin fingerprint does not match its evidence")
        return self
