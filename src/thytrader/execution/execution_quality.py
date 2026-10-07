"""Read-only live round-trip cost attribution and execution-quality evidence (ADR 0116).

This module folds one deployment's recorded fills into closed round trips and reports,
per trip and in total: fill-price PnL before fees, exact recorded entry and exit fees,
net PnL, and execution slippage against the persisted intent's completed decision
bar. A fill bar's future close is never a causal reference. It never mutates historical
fills, never invents missing fees, liquidity, marks, or closes, and never treats absent
evidence as zero. Anything it cannot prove is disclosed as an evidence reason instead.

The fold mirrors the fill ledger's lot semantics (buys open or add to a long and cover
a short; sells mirror), while sums stay direct: each round trip's numbers are exact
sums of its recorded fills. The ledger also realizes partial exits before their cycle
closes; fee-allocation rounding and over-cover clamping can add differences too. The
residual versus closed-cycle totals is disclosed as
``ledger_realized_delta`` exactly like the backtest cost attribution's
``summary_net_pnl_delta``; it is never silently attributed to fees.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, Inexact, InvalidOperation, Overflow, localcontext
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import TYPE_CHECKING, Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_serializer, model_validator

from thytrader.execution.decision_store import (
    DecisionJournalStore,
    decision_storage_label,
)
from thytrader.execution.decisions import DECISION_PAGE_MAX_LIMIT
from thytrader.execution.ledger import (
    DeploymentLedger,
    effective_paper_fee_rates,
    ledger_from_snapshot,
)
from thytrader.execution.models import (
    DeploymentMode,
    DeploymentSnapshot,
    IntentPurpose,
    OrderKind,
    OrderSide,
    PositionSide,
    resolved_product_id,
    snapshot_positions,
)
from thytrader.execution.twins import TwinValidationError, comparable_twins
from thytrader.market_data.models import parse_candle_interval
from thytrader.research.indicators import canonical_decimal

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from thytrader.execution.models import Deployment, Fill, Order, OrderIntent, Position
    from thytrader.execution.twins import DeploymentTwinLink
    from thytrader.strategies.snapshots import StrategySnapshot

EXECUTION_QUALITY_SCHEMA_VERSION: Literal["thytrader-execution-quality-v1"] = (
    "thytrader-execution-quality-v1"
)
EXECUTION_TWIN_SCHEMA_VERSION: Literal["thytrader-execution-twin-comparison-v1"] = (
    "thytrader-execution-twin-comparison-v1"
)
_PLACEHOLDER_FINGERPRINT = "sha256:" + "0" * 64
_PLAIN_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_BPS = Decimal(10000)
_ZERO = Decimal(0)
_SLIPPAGE_CONTEXT = Context(prec=64, Emin=-6143, Emax=6144)
_DECISION_FETCH_MAX_PAGES = 25
JOURNALED_CLOSE_PAGE_LIMIT = DECISION_PAGE_MAX_LIMIT

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


def _sum_exact(values: Iterable[Decimal]) -> Decimal:
    """Sum recorded decimals exactly, independent of the ambient Decimal context."""
    materialized = tuple(values)
    if not materialized:
        return _ZERO
    lowest = min(value.adjusted() - len(value.as_tuple().digits) + 1 for value in materialized)
    highest = max(value.adjusted() for value in materialized)
    precision = highest - lowest + len(str(len(materialized))) + 3
    exact = Context(
        prec=precision,
        Emin=-20000,
        Emax=20000,
        traps=[Inexact, InvalidOperation, Overflow],
    )
    with localcontext(exact):
        return sum(materialized, start=_ZERO)


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
class _DecisionReference:
    """The persisted intent and completed bar that causally anchor a fill benchmark."""

    price: Decimal
    intent_id: UUID
    bar_starts_at: datetime
    bar_closes_at: datetime


@dataclass(frozen=True, slots=True)
class _RecordedFill:
    """One applied fill plus the liquidity, side, and slippage evidence proven for it."""

    fill: Fill
    order_side: OrderSide
    liquidity: MakerTakerEvidence | None
    slippage_bps: Decimal | None
    reference: _DecisionReference | None = None


@dataclass(slots=True)
class _CycleFold:
    """Direct-sum cycle accumulation over recorded fills (no average-cost rounding)."""

    direction: CycleDirection | None = None
    quantity: Decimal = _ZERO
    entry_notional: Decimal = _ZERO
    entry_fees: Decimal = _ZERO
    exit_notional: Decimal = _ZERO
    exit_fees: Decimal = _ZERO
    entries: list[_RecordedFill] = field(default_factory=list)
    exits: list[_RecordedFill] = field(default_factory=list)
    opened_at: datetime | None = None


@dataclass(slots=True)
class _BookFold:
    """Per-product accumulation of closed trips and the current open cycle."""

    product_id: str
    recorded_fills: list[_RecordedFill] = field(default_factory=list)
    cycle: _CycleFold = field(default_factory=_CycleFold)
    trips: list[tuple[_CycleFold, datetime]] = field(default_factory=list)


def _liquidity_evidence(order: Order) -> MakerTakerEvidence | None:
    """Classify liquidity only when the recorded order kind proves it."""
    if order.kind is OrderKind.POST_ONLY_LIMIT:
        return "maker"
    if order.kind is OrderKind.MARKETABLE:
        return "taker"
    return None


def _bar_start(instant: datetime, duration: timedelta) -> datetime:
    """Floor one UTC instant to its decision-clock bar start."""
    seconds = duration.total_seconds()
    offset = instant.timestamp() % seconds
    return (instant - timedelta(seconds=offset)).replace(tzinfo=UTC)


def _signed_slippage_bps(fill: Fill, side: OrderSide, close: Decimal) -> Decimal | None:
    """Signed slippage of one fill against a journaled close, positive when worse."""
    if close <= 0:
        return None
    with localcontext(_SLIPPAGE_CONTEXT):
        difference = fill.price - close if side is OrderSide.BUY else close - fill.price
        return difference / close * _BPS


def _weighted_slippage(weighted: Sequence[tuple[Decimal | None, Decimal]]) -> Decimal | None:
    """Quantity-weighted mean slippage over fills that have one; None when none do."""
    proven = [(bps, quantity) for bps, quantity in weighted if bps is not None]
    if not proven:
        return None
    total_quantity = _sum_exact(quantity for _bps, quantity in proven)
    with localcontext(_SLIPPAGE_CONTEXT):
        return sum((bps * quantity for bps, quantity in proven), start=_ZERO) / total_quantity


def build_execution_quality_report(
    snapshot: DeploymentSnapshot,
    *,
    journaled_closes: Mapping[tuple[str, datetime], JournaledDecisionClose] | None = None,
    decision_coverage: tuple[datetime, datetime] | None = None,
    decision_rows_fetched: int | None = None,
    journaled_entry_signals: int | None = None,
    ledger: DeploymentLedger | None = None,
    extra_reasons: Sequence[ExecutionQualityEvidenceReason] = (),
) -> ExecutionQualityReport:
    """Fold one deployment snapshot into read-only execution-quality evidence.

    Args:
        snapshot: The full deployment snapshot (deployment, orders, fills, positions).
        journaled_closes: Optional ``(product_id, bar_starts_at)`` → close and completion
            instant. Only the intent's completed decision bar can be a causal reference;
            missing and future references are disclosed, never defaulted.
        decision_coverage: The ``(oldest_bar, newest_bar)`` window the fetched decision
            rows cover, when a journal was readable.
        decision_rows_fetched: How many decision rows were read, for bounded paging.
        journaled_entry_signals: Entry-signal rows seen in the fetched journal pages, or
            None when the journal could not be read.
        ledger: Optional precomputed ledger; when omitted it is derived without marks.
        extra_reasons: Route-level facts the fold cannot see (journal unavailable,
            bounded paging stopped early), appended to the evidence reasons.

    Returns:
        One frozen report whose fingerprint binds its full content.
    """
    closes = dict(journaled_closes) if journaled_closes is not None else {}
    deployment = snapshot.deployment
    reasons: list[ExecutionQualityEvidenceReason] = list(dict.fromkeys(extra_reasons))
    orders = {order.id: order for order in snapshot.orders}
    duration = _deployment_bar_duration(deployment, reasons)
    marks_ledger = ledger if ledger is not None else ledger_from_snapshot(snapshot)
    positions = positions_by_product(snapshot)
    applied, unapplied, orphan = _partition_fills(snapshot, orders)
    if unapplied:
        reasons.append(ExecutionQualityEvidenceReason.UNAPPLIED_FILL_ECONOMICS)
    if orphan:
        reasons.append(ExecutionQualityEvidenceReason.FILL_WITHOUT_ORDER)
    books_fold = _fold_books(
        applied,
        deployment=deployment,
        closes=closes,
        duration=duration,
        intents={intent.id: intent for intent in snapshot.intents},
        reasons=reasons,
    )
    books = _append_position_only_books(
        tuple(_book_response(fold, positions.get(fold.product_id)) for fold in books_fold),
        positions,
        reasons,
    )
    reasons.extend(_position_reasons(books, positions))
    totals = _totals_response(
        books,
        marks_ledger,
        applied,
        snapshot,
        reasons,
        journaled_entry_signals=journaled_entry_signals,
    )
    if any(book.open_cycle is not None for book in books):
        reasons.append(ExecutionQualityEvidenceReason.OPEN_CYCLE_PRESENT)
    evidence = ExecutionQualityEvidence(
        complete=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        journaled_close_coverage=_coverage_response(
            closes, decision_coverage, decision_rows_fetched
        ),
        unapplied_fill_count=unapplied,
        orphan_fill_count=orphan,
    )
    return ExecutionQualityReport(
        deployment_id=deployment.id,
        product_id=deployment.product_id,
        mode=deployment.mode,
        status=deployment.status.value,
        strategy_fingerprint=deployment.strategy_fingerprint,
        timeframe=deployment.timeframe,
        books=books,
        totals=totals,
        evidence=evidence,
    )


def _coverage_response(
    closes: Mapping[tuple[str, datetime], JournaledDecisionClose],
    decision_coverage: tuple[datetime, datetime] | None,
    rows_fetched: int | None,
) -> JournaledCloseCoverage | None:
    """Build the journal coverage disclosure, or None when nothing was readable."""
    if decision_coverage is not None:
        return JournaledCloseCoverage(
            oldest_bar=decision_coverage[0],
            newest_bar=decision_coverage[1],
            bars=rows_fetched if rows_fetched is not None else len(closes),
        )
    if not closes:
        return None
    bars = [bar for _product, bar in closes]
    return JournaledCloseCoverage(
        oldest_bar=min(bars), newest_bar=max(bars), bars=rows_fetched or len(closes)
    )


def _deployment_bar_duration(
    deployment: Deployment, reasons: list[ExecutionQualityEvidenceReason]
) -> timedelta | None:
    """Resolve the decision-clock duration; unknown clocks disable close matching."""
    if not deployment.timeframe:
        reasons.append(ExecutionQualityEvidenceReason.TIMEFRAME_UNKNOWN)
        return None
    try:
        return parse_candle_interval(deployment.timeframe).duration
    except ValueError:
        reasons.append(ExecutionQualityEvidenceReason.TIMEFRAME_UNKNOWN)
        return None


def _partition_fills(
    snapshot: DeploymentSnapshot, orders: Mapping[UUID, Order]
) -> tuple[tuple[tuple[Fill, Order], ...], int, int]:
    """Split fills into applied evidence, unapplied economics, and orphan rows."""
    applied: list[tuple[Fill, Order]] = []
    unapplied = 0
    orphan = 0
    for fill in sorted(snapshot.fills, key=lambda item: (item.filled_at, item.venue_fill_id)):
        order = orders.get(fill.order_id)
        if order is None:
            orphan += 1
            continue
        if fill.economics_applied_at is None:
            unapplied += 1
            continue
        applied.append((fill, order))
    return tuple(applied), unapplied, orphan


def _fold_books(
    applied: Sequence[tuple[Fill, Order]],
    *,
    deployment: Deployment,
    closes: Mapping[tuple[str, datetime], JournaledDecisionClose],
    duration: timedelta | None,
    intents: Mapping[UUID, OrderIntent],
    reasons: list[ExecutionQualityEvidenceReason],
) -> list[_BookFold]:
    """Apply every applied fill to its product book in recorded order."""
    books: dict[str, _BookFold] = {}
    for fill, order in applied:
        product_id = resolved_product_id(order.product_id, deployment)
        book = books.setdefault(product_id, _BookFold(product_id=product_id))
        liquidity = _liquidity_evidence(order)
        if liquidity is None:
            reasons.append(ExecutionQualityEvidenceReason.LIQUIDITY_NOT_RECORDED)
        reference = _causal_reference(
            fill, order, intents.get(order.intent_id), deployment, closes, duration, reasons
        )
        recorded = _RecordedFill(
            fill=fill,
            order_side=order.side,
            liquidity=liquidity,
            slippage_bps=None
            if reference is None
            else _signed_slippage_bps(fill, order.side, reference.price),
            reference=reference,
        )
        book.recorded_fills.append(recorded)
        _apply_fill(book, recorded, reasons)
    return [books[key] for key in sorted(books)]


def _causal_reference(
    fill: Fill,
    order: Order,
    intent: OrderIntent | None,
    deployment: Deployment,
    closes: Mapping[tuple[str, datetime], JournaledDecisionClose],
    duration: timedelta | None,
    reasons: list[ExecutionQualityEvidenceReason],
) -> _DecisionReference | None:
    """Use only the intent's own completed bar; never substitute a fill-time close."""
    if intent is None:
        reasons.append(ExecutionQualityEvidenceReason.FILL_WITHOUT_INTENT)
        return None
    close = closes.get((resolved_product_id(order.product_id, deployment), intent.candle_starts_at))
    if close is None or duration is None:
        reasons.append(ExecutionQualityEvidenceReason.FILL_WITHOUT_JOURNALED_CLOSE)
        return None
    if (
        order.parent_order_id is not None
        or intent.side is not order.side
        or intent.deployment_id != deployment.id
        or resolved_product_id(intent.product_id, deployment)
        != resolved_product_id(order.product_id, deployment)
        or not intent.created_at <= order.created_at <= fill.filled_at
        or close.price <= 0
        or close.bar_closes_at != intent.candle_starts_at + duration
        or close.bar_closes_at > min(intent.created_at, order.created_at, fill.filled_at)
    ):
        reasons.append(ExecutionQualityEvidenceReason.NON_CAUSAL_DECISION_REFERENCE)
        return None
    return _DecisionReference(
        price=close.price,
        intent_id=intent.id,
        bar_starts_at=intent.candle_starts_at,
        bar_closes_at=close.bar_closes_at,
    )


def _apply_fill(
    book: _BookFold, recorded: _RecordedFill, reasons: list[ExecutionQualityEvidenceReason]
) -> None:
    """Apply one fill as an entry, an add, or a (possibly clamped) exit."""
    fill = recorded.fill
    cycle = book.cycle
    quantity = fill.quantity
    if cycle.direction is None:
        cycle.direction = "long" if recorded.order_side is OrderSide.BUY else "short"
        cycle.opened_at = fill.filled_at
    is_entry_side = (cycle.direction == "long") is (recorded.order_side is OrderSide.BUY)
    if is_entry_side:
        cycle.quantity += quantity
        cycle.entry_notional += fill.price * quantity
        cycle.entry_fees += fill.fee
        cycle.entries.append(recorded)
        return
    covered = min(quantity, cycle.quantity)
    if covered < quantity:
        reasons.append(ExecutionQualityEvidenceReason.POSITION_FLIP_FILL)
    if covered > 0:
        cycle.exit_notional += fill.price * covered
        cycle.exit_fees += fill.fee
        cycle.exits.append(
            _RecordedFill(
                fill=replace(fill, quantity=covered),
                order_side=recorded.order_side,
                liquidity=recorded.liquidity,
                slippage_bps=recorded.slippage_bps,
                reference=recorded.reference,
            )
        )
    cycle.quantity -= covered
    if cycle.quantity == 0:
        book.trips.append((cycle, fill.filled_at))
        book.cycle = _CycleFold()


def positions_by_product(snapshot: DeploymentSnapshot) -> dict[str, Position]:
    """Index the snapshot's product books by resolved product id."""
    return {
        resolved_product_id(position.product_id, snapshot.deployment): position
        for position in snapshot_positions(snapshot)
    }


def _signed_position_quantity(position: Position) -> Decimal:
    """Signed inventory quantity, negative for shorts."""
    if position.side is PositionSide.SHORT:
        return -position.quantity
    return position.quantity


def _signed_cycle_quantity(cycle: _CycleFold, direction: CycleDirection) -> Decimal:
    """Signed remaining quantity of one cycle."""
    return cycle.quantity if direction == "long" else -cycle.quantity


def _fill_response(recorded: _RecordedFill) -> ExecutionQualityFill:
    """Map one recorded fill to its frozen response shape."""
    fill = recorded.fill
    return ExecutionQualityFill(
        fill_id=fill.id,
        order_id=fill.order_id,
        side=recorded.order_side,
        price=canonical_decimal(fill.price),
        quantity=canonical_decimal(fill.quantity),
        fee=canonical_decimal(fill.fee),
        filled_at=fill.filled_at,
        liquidity=recorded.liquidity,
        slippage_bps=None
        if recorded.slippage_bps is None
        else canonical_decimal(recorded.slippage_bps),
        reference_price=None
        if recorded.reference is None
        else canonical_decimal(recorded.reference.price),
        reference_intent_id=None if recorded.reference is None else recorded.reference.intent_id,
        reference_bar_starts_at=None
        if recorded.reference is None
        else recorded.reference.bar_starts_at,
        reference_bar_closes_at=None
        if recorded.reference is None
        else recorded.reference.bar_closes_at,
    )


def _trip_response(cycle: _CycleFold, closed_at: datetime) -> ExecutionQualityRoundTrip:
    """Freeze one closed cycle with direct sums of its recorded fills."""
    if cycle.direction is None:
        raise ValueError("A closed round trip must have a recorded opening fill.")
    direction: CycleDirection = cycle.direction
    before_fees = (
        cycle.exit_notional - cycle.entry_notional
        if direction == "long"
        else cycle.entry_notional - cycle.exit_notional
    )
    net = before_fees - cycle.entry_fees - cycle.exit_fees
    fills = (*cycle.entries, *cycle.exits)
    weighted = _weighted_slippage([(item.slippage_bps, item.fill.quantity) for item in fills])
    return ExecutionQualityRoundTrip(
        direction=direction,
        opened_at=cycle.opened_at or closed_at,
        closed_at=closed_at,
        closed_quantity=canonical_decimal(_sum_exact(item.fill.quantity for item in cycle.entries)),
        entries=tuple(_fill_response(item) for item in cycle.entries),
        exits=tuple(_fill_response(item) for item in cycle.exits),
        fill_price_pnl_before_fees=canonical_decimal(before_fees),
        entry_fees=canonical_decimal(cycle.entry_fees),
        exit_fees=canonical_decimal(cycle.exit_fees),
        net_pnl=canonical_decimal(net),
        slippage_bps=None if weighted is None else canonical_decimal(weighted),
        slippage_fills_journaled=sum(1 for item in fills if item.slippage_bps is not None),
        slippage_fills_total=len(fills),
    )


def _open_cycle_response(cycle: _CycleFold, position: Position | None) -> ExecutionQualityOpenCycle:
    """Freeze the still-open cycle without inventing any exit or mark.

    Callers only pass cycles whose direction a recorded fill set, so the opening
    instant is always present; falling back to wall-clock time here would instead
    make the report fingerprint nondeterministic.
    """
    if cycle.direction is None or cycle.opened_at is None:
        raise ValueError("An open cycle must have a recorded opening fill.")
    direction: CycleDirection = cycle.direction
    signed = _signed_cycle_quantity(cycle, direction)
    matches = position is not None and _signed_position_quantity(position) == signed
    return ExecutionQualityOpenCycle(
        direction=direction,
        opened_at=cycle.opened_at,
        quantity=canonical_decimal(signed),
        entry_notional=canonical_decimal(cycle.entry_notional),
        entry_fees=canonical_decimal(cycle.entry_fees),
        entries=tuple(_fill_response(item) for item in cycle.entries),
        position_matches_ledger=matches,
        exits=tuple(_fill_response(item) for item in cycle.exits),
    )


def _book_response(fold: _BookFold, position: Position | None) -> ExecutionQualityBook:
    """Freeze one product book with its closed-trade totals."""
    trips = tuple(_trip_response(cycle, closed_at) for cycle, closed_at in fold.trips)
    return ExecutionQualityBook(
        product_id=fold.product_id,
        closed_trade_count=len(trips),
        round_trips=trips,
        recorded_fills=tuple(_fill_response(item) for item in fold.recorded_fills),
        open_cycle=None
        if fold.cycle.direction is None
        else _open_cycle_response(fold.cycle, position),
        fill_price_pnl_before_fees=canonical_decimal(
            _sum_exact(Decimal(trip.fill_price_pnl_before_fees) for trip in trips)
        ),
        entry_fees=canonical_decimal(_sum_exact(Decimal(trip.entry_fees) for trip in trips)),
        exit_fees=canonical_decimal(_sum_exact(Decimal(trip.exit_fees) for trip in trips)),
        net_pnl=canonical_decimal(_sum_exact(Decimal(trip.net_pnl) for trip in trips)),
    )


def _append_position_only_books(
    books: tuple[ExecutionQualityBook, ...],
    positions: Mapping[str, Position],
    reasons: list[ExecutionQualityEvidenceReason],
) -> tuple[ExecutionQualityBook, ...]:
    """Disclose product books whose position row has no fill evidence at all."""
    known = {book.product_id for book in books}
    extras: list[ExecutionQualityBook] = []
    for product_id in sorted(set(positions) - known):
        if positions[product_id].quantity == 0:
            continue
        reasons.append(ExecutionQualityEvidenceReason.POSITION_WITHOUT_FILL_EVIDENCE)
        extras.append(
            ExecutionQualityBook(
                product_id=product_id,
                closed_trade_count=0,
                fill_price_pnl_before_fees="0",
                entry_fees="0",
                exit_fees="0",
                net_pnl="0",
            )
        )
    return (*books, *extras)


def _position_reasons(
    books: Sequence[ExecutionQualityBook], positions: Mapping[str, Position]
) -> list[ExecutionQualityEvidenceReason]:
    """Flag open cycles that disagree with, or lack, the persisted position rows."""
    reasons: list[ExecutionQualityEvidenceReason] = []
    by_product = {book.product_id: book for book in books}
    for product_id, book in by_product.items():
        if book.open_cycle is None:
            continue
        position = positions.get(product_id)
        if position is None:
            reasons.append(ExecutionQualityEvidenceReason.OPEN_CYCLE_WITHOUT_POSITION_ROW)
        elif not book.open_cycle.position_matches_ledger:
            reasons.append(ExecutionQualityEvidenceReason.OPEN_POSITION_MISMATCH)
    return reasons


def _totals_response(
    books: Sequence[ExecutionQualityBook],
    ledger: DeploymentLedger,
    applied: Sequence[tuple[Fill, Order]],
    snapshot: DeploymentSnapshot,
    reasons: list[ExecutionQualityEvidenceReason],
    *,
    journaled_entry_signals: int | None,
) -> ExecutionQualityTotals:
    """Aggregate closed-trade sums and the ledger disclosure delta."""
    trips = [trip for book in books for trip in book.round_trips]
    net_total = _sum_exact(Decimal(trip.net_pnl) for trip in trips)
    delta = ledger.realized_net_pnl - net_total
    if delta != 0:
        reasons.append(ExecutionQualityEvidenceReason.LEDGER_REALIZATION_DELTA)
    recorded = [fill for book in books for fill in book.recorded_fills]
    journaled = sum(fill.slippage_bps is not None for fill in recorded)
    total_fills = len(recorded)
    weighted = _weighted_slippage(
        [
            (
                None if fill.slippage_bps is None else Decimal(fill.slippage_bps),
                Decimal(fill.quantity),
            )
            for fill in recorded
        ]
    )
    instants = [fill.filled_at for fill, _order in applied]
    return ExecutionQualityTotals(
        closed_trade_count=len(trips),
        open_cycle_count=sum(1 for book in books if book.open_cycle is not None),
        applied_fill_count=len(applied),
        fill_price_pnl_before_fees=canonical_decimal(
            _sum_exact(Decimal(trip.fill_price_pnl_before_fees) for trip in trips)
        ),
        entry_fees=canonical_decimal(_sum_exact(Decimal(trip.entry_fees) for trip in trips)),
        exit_fees=canonical_decimal(_sum_exact(Decimal(trip.exit_fees) for trip in trips)),
        net_pnl=canonical_decimal(net_total),
        ledger_realized_delta=canonical_decimal(delta),
        weighted_slippage_bps=None if weighted is None else canonical_decimal(weighted),
        slippage_fills_journaled=journaled,
        slippage_fills_total=total_fills,
        unfilled_entry_orders=_unfilled_entry_orders(snapshot, applied),
        journaled_entry_signals=journaled_entry_signals,
        first_fill_at=min(instants) if instants else None,
        last_fill_at=max(instants) if instants else None,
    )


def _unfilled_entry_orders(
    snapshot: DeploymentSnapshot, applied: Sequence[tuple[Fill, Order]]
) -> int:
    """Count entry orders with no applied fill. These are unmatched, not expired-as-zero."""
    entry_intents = {
        intent.id for intent in snapshot.intents if intent.purpose is IntentPurpose.ENTRY
    }
    filled_orders = {order.id for _fill, order in applied}
    return sum(
        1
        for order in snapshot.orders
        if order.intent_id in entry_intents and order.id not in filled_orders
    )


@dataclass(frozen=True, slots=True)
class JournaledCloseEvidence:
    """Journaled closes plus the bounded window the fetched rows covered."""

    closes: dict[tuple[str, datetime], JournaledDecisionClose]
    coverage: tuple[datetime, datetime] | None
    rows_fetched: int
    coverage_limited: bool


async def load_journaled_close_evidence(
    store: DecisionJournalStore,
    *,
    deployment_id: UUID,
    snapshot: DeploymentSnapshot,
    page_limit: int = JOURNALED_CLOSE_PAGE_LIMIT,
    max_pages: int = _DECISION_FETCH_MAX_PAGES,
) -> JournaledCloseEvidence:
    """Page the decision journal for the closes needed by one snapshot's fills.

    Paging is bounded: at most ``max_pages`` newest-first pages per product are read,
    stopping early once rows reach the earliest applied fill's original intent bar.
    A missing journal yields no closes and no coverage, never a default.
    """
    if decision_storage_label(store) == "unavailable":
        return JournaledCloseEvidence({}, None, 0, False)
    deployment = snapshot.deployment
    orders = {order.id: order for order in snapshot.orders}
    earliest = _earliest_fill_bars(snapshot, deployment, orders)
    products = sorted(earliest)
    closes: dict[tuple[str, datetime], JournaledDecisionClose] = {}
    rows = 0
    limited = False
    for product_id in products:
        cursor: str | None = None
        exhausted = False
        for _page in range(max_pages):
            page = await store.list_for_deployment(
                deployment_id, limit=page_limit, cursor=cursor, product_id=product_id
            )
            for decision in page.decisions:
                rows += 1
                if decision.close_price is not None:
                    closes[(product_id, decision.bar_starts_at)] = JournaledDecisionClose(
                        price=Decimal(decision.close_price), bar_closes_at=decision.bar_closes_at
                    )
            cursor = page.next_cursor
            if cursor is None:
                exhausted = True
                break
            oldest_seen = page.decisions[-1].bar_starts_at if page.decisions else None
            if oldest_seen is not None and oldest_seen <= earliest[product_id]:
                exhausted = True  # All required intent bars were reached, not cap-limited.
                break
        if not exhausted:
            limited = True
    coverage = None
    if closes:
        bars = [bar for _product, bar in closes]
        coverage = (min(bars), max(bars))
    return JournaledCloseEvidence(closes, coverage, rows, limited)


def _earliest_fill_bars(
    snapshot: DeploymentSnapshot,
    deployment: Deployment,
    orders: Mapping[UUID, Order],
) -> dict[str, datetime]:
    """Find the earliest decision bar needed by applied fills, not their later fill bars."""
    earliest: dict[str, datetime] = {}
    intents = {intent.id: intent for intent in snapshot.intents}
    for fill in snapshot.fills:
        order = orders.get(fill.order_id)
        if order is None or fill.economics_applied_at is None:
            continue
        product_id = resolved_product_id(order.product_id, deployment)
        intent = intents.get(order.intent_id)
        if intent is None:
            continue
        bar = intent.candle_starts_at
        current = earliest.get(product_id)
        if current is None or bar < current:
            earliest[product_id] = bar
    return earliest


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


def build_execution_twin_comparison(
    *,
    link: DeploymentTwinLink,
    paper_snapshot: DeploymentSnapshot,
    paper_report: ExecutionQualityReport,
    live_snapshot: DeploymentSnapshot,
    live_report: ExecutionQualityReport,
    strategy_snapshots: tuple[StrategySnapshot, StrategySnapshot] | None = None,
) -> ExecutionTwinComparison:
    """Compare two explicitly linked books on recorded execution evidence only.

    Lifetime summaries are context only unless the complete books share identical
    fill bounds and proven decision/product/side/quantity populations. Rules must be
    identical or proven equivalent by ADR 0105 snapshots. Fee normalization always
    describes all applied lifetime live fills, never a silently cropped overlap.

    Raises:
        ValueError: When the link's members do not match the supplied reports.
    """
    reasons: list[TwinComparisonReason] = []
    _require_twin_member(
        link.paper_deployment_id, DeploymentMode.PAPER, paper_snapshot, paper_report
    )
    _require_twin_member(link.live_deployment_id, DeploymentMode.LIVE, live_snapshot, live_report)
    paper_deployment = paper_snapshot.deployment
    live_deployment = live_snapshot.deployment
    _twin_rule_reasons(paper_deployment, live_deployment, strategy_snapshots, reasons)
    overlap = _twin_overlap(paper_report, live_report)
    if overlap is None:
        reasons.append(TwinComparisonReason.NO_OVERLAPPING_FILLS)
    if not paper_report.evidence.complete:
        reasons.append(TwinComparisonReason.INCOMPLETE_PAPER_EVIDENCE)
    if not live_report.evidence.complete:
        reasons.append(TwinComparisonReason.INCOMPLETE_LIVE_EVIDENCE)
    paper_side = _twin_side(paper_report)
    live_side = _twin_side(live_report)
    _twin_population_reasons(paper_snapshot, paper_report, live_snapshot, live_report, reasons)
    normalization = _fee_normalization(paper_snapshot, live_report, reasons)
    informational = {
        TwinComparisonReason.SNAPSHOT_FINGERPRINTS_DIFFER,
        TwinComparisonReason.PAPER_FEE_RATES_DEFAULTED,
    }
    comparable = not any(reason not in informational for reason in reasons)
    return ExecutionTwinComparison(
        paper=paper_side,
        live=live_side,
        strategy_fingerprint_paper=paper_deployment.strategy_fingerprint,
        strategy_fingerprint_live=live_deployment.strategy_fingerprint,
        product_id=live_deployment.product_id,
        timeframe=live_deployment.timeframe,
        overlap=overlap,
        comparable=comparable,
        summaries_context_only=not comparable,
        reasons=tuple(dict.fromkeys(reasons)),
        fee_normalization=normalization,
    )


def _require_twin_member(
    member_id: UUID,
    mode: DeploymentMode,
    snapshot: DeploymentSnapshot,
    report: ExecutionQualityReport,
) -> None:
    """Reject reports or snapshots that do not represent the exact linked member."""
    deployment = snapshot.deployment
    if (
        member_id != deployment.id
        or member_id != report.deployment_id
        or deployment.mode is not mode
        or report.mode is not mode
        or report.product_id != deployment.product_id
        or report.timeframe != deployment.timeframe
        or report.strategy_fingerprint != deployment.strategy_fingerprint
    ):
        raise ValueError("Twin link members do not match the supplied snapshots and reports.")
    applied, _unapplied, _orphan = _partition_fills(snapshot, {o.id: o for o in snapshot.orders})
    actual = {fill.fill_id: fill for book in report.books for fill in book.recorded_fills}
    if len(actual) != len(applied) or any(
        fill.id not in actual or not _record_matches_fill(actual[fill.id], fill, order)
        for fill, order in applied
    ):
        raise ValueError("Twin report fill population does not match its source snapshot.")


def _record_matches_fill(record: ExecutionQualityFill, fill: Fill, order: Order) -> bool:
    """Validate raw fill facts, not cycle projections whose exits may be clamped."""
    return (
        record.order_id == fill.order_id
        and record.side is order.side
        and record.price == canonical_decimal(fill.price)
        and record.quantity == canonical_decimal(fill.quantity)
        and record.fee == canonical_decimal(fill.fee)
        and record.filled_at == fill.filled_at
        and record.liquidity == _liquidity_evidence(order)
    )


def _twin_rule_reasons(
    paper: Deployment,
    live: Deployment,
    snapshots: tuple[StrategySnapshot, StrategySnapshot] | None,
    reasons: list[TwinComparisonReason],
) -> None:
    """Apply ADR 0105's pinned-content proof, not snapshot-name or link trust."""
    differing = paper.strategy_fingerprint != live.strategy_fingerprint
    if differing:
        reasons.append(TwinComparisonReason.SNAPSHOT_FINGERPRINTS_DIFFER)
    try:
        comparable_twins(paper, live, snapshots=snapshots)
    except TwinValidationError:
        reasons.append(
            TwinComparisonReason.TRADING_RULES_UNVERIFIED
            if differing and snapshots is None
            else TwinComparisonReason.TRADING_RULES_INCOMPATIBLE
        )


@dataclass(frozen=True, slots=True)
class _FillPopulationKey:
    """Comparable fill exposure, excluding price/fee outcomes and deployment identity."""

    product_id: str
    decision_bar: datetime
    purpose: IntentPurpose
    side: OrderSide
    quantity: Decimal


def _fill_population(snapshot: DeploymentSnapshot) -> Counter[_FillPopulationKey] | None:
    """Describe every applied fill's proven intent; never align missing intents by time."""
    intents = {intent.id: intent for intent in snapshot.intents}
    applied, _unapplied, _orphan = _partition_fills(snapshot, {o.id: o for o in snapshot.orders})
    population: Counter[_FillPopulationKey] = Counter()
    for fill, order in applied:
        intent = intents.get(order.intent_id)
        if intent is None:
            return None
        population[
            _FillPopulationKey(
                product_id=resolved_product_id(order.product_id, snapshot.deployment),
                decision_bar=intent.candle_starts_at,
                purpose=intent.purpose,
                side=order.side,
                quantity=fill.quantity,
            )
        ] += 1
    return population


def _order_population(snapshot: DeploymentSnapshot) -> Counter[_FillPopulationKey] | None:
    """Include unfilled orders' decision exposure, not just their equal aggregate counts."""
    intents = {intent.id: intent for intent in snapshot.intents}
    population: Counter[_FillPopulationKey] = Counter()
    for order in snapshot.orders:
        intent = intents.get(order.intent_id)
        if intent is None:
            return None
        population[
            _FillPopulationKey(
                product_id=resolved_product_id(order.product_id, snapshot.deployment),
                decision_bar=intent.candle_starts_at,
                purpose=intent.purpose,
                side=order.side,
                quantity=order.quantity,
            )
        ] += 1
    return population


def _twin_population_reasons(
    paper_snapshot: DeploymentSnapshot,
    paper: ExecutionQualityReport,
    live_snapshot: DeploymentSnapshot,
    live: ExecutionQualityReport,
    reasons: list[TwinComparisonReason],
) -> None:
    """Block unequal lifetime histories even if some of their fill dates overlap."""
    if (paper.totals.first_fill_at, paper.totals.last_fill_at) != (
        live.totals.first_fill_at,
        live.totals.last_fill_at,
    ):
        reasons.append(TwinComparisonReason.LIFETIME_WINDOWS_DIFFER)
    first = _fill_population(paper_snapshot)
    second = _fill_population(live_snapshot)
    if first is None or second is None:
        reasons.append(TwinComparisonReason.FILL_POPULATION_UNVERIFIED)
    elif first != second:
        reasons.append(TwinComparisonReason.FILL_POPULATIONS_DIFFER)
    paper_orders = _order_population(paper_snapshot)
    live_orders = _order_population(live_snapshot)
    if paper_orders is None or live_orders is None:
        reasons.append(TwinComparisonReason.ORDER_POPULATION_UNVERIFIED)
    elif paper_orders != live_orders:
        reasons.append(TwinComparisonReason.ORDER_POPULATIONS_DIFFER)
    if sum(_entry_fill_count(book) for book in paper.books) != sum(
        _entry_fill_count(book) for book in live.books
    ):
        reasons.append(TwinComparisonReason.ENTRY_FILL_COUNT_DIVERGENCE)
    if paper.totals.unfilled_entry_orders != live.totals.unfilled_entry_orders:
        reasons.append(TwinComparisonReason.UNFILLED_ENTRY_ORDER_DIVERGENCE)
    if paper.totals.journaled_entry_signals != live.totals.journaled_entry_signals:
        reasons.append(TwinComparisonReason.ENTRY_SIGNAL_COUNT_DIVERGENCE)


def _twin_side(report: ExecutionQualityReport) -> ExecutionTwinSide:
    """Project one report into the twin comparison's per-side summary."""
    totals = report.totals
    return ExecutionTwinSide(
        deployment_id=report.deployment_id,
        mode=report.mode,
        status=report.status,
        evidence_complete=report.evidence.complete,
        closed_trade_count=totals.closed_trade_count,
        entry_fill_count=sum(_entry_fill_count(book) for book in report.books),
        applied_fill_count=totals.applied_fill_count,
        unfilled_entry_orders=totals.unfilled_entry_orders,
        journaled_entry_signals=totals.journaled_entry_signals,
        fill_price_pnl_before_fees=totals.fill_price_pnl_before_fees,
        entry_fees=totals.entry_fees,
        exit_fees=totals.exit_fees,
        net_pnl=totals.net_pnl,
        weighted_slippage_bps=totals.weighted_slippage_bps,
        first_fill_at=totals.first_fill_at,
        last_fill_at=totals.last_fill_at,
    )


def _entry_fill_count(book: ExecutionQualityBook) -> int:
    """Count fills that opened or added to a position cycle in one book."""
    count = sum(len(trip.entries) for trip in book.round_trips)
    if book.open_cycle is not None:
        count += len(book.open_cycle.entries)
    return count


def _twin_overlap(
    paper: ExecutionQualityReport, live: ExecutionQualityReport
) -> TwinOverlapWindow | None:
    """Intersect the two books' recorded fill windows; None when disjoint."""
    first: datetime | None = None
    last: datetime | None = None
    for report in (paper, live):
        bounds = report.totals
        if bounds.first_fill_at is None or bounds.last_fill_at is None:
            return None
        first = bounds.first_fill_at if first is None else max(first, bounds.first_fill_at)
        last = bounds.last_fill_at if last is None else min(last, bounds.last_fill_at)
    if first is None or last is None or last < first:
        return None
    return TwinOverlapWindow(first_fill_at=first, last_fill_at=last)


def _fee_normalization(
    paper_snapshot: DeploymentSnapshot,
    live_report: ExecutionQualityReport,
    reasons: list[TwinComparisonReason],
) -> ExecutionFeeNormalization | None:
    """Re-price recorded live fills at the paper book's fee assumptions.

    Realized live fees stay untouched; this is a disclosed counterfactual over the
    entire applied-fill lifetime. Unknown liquidity makes the total unavailable.
    """
    deployment = paper_snapshot.deployment
    maker, taker = effective_paper_fee_rates(
        deployment.paper_maker_fee_rate, deployment.paper_taker_fee_rate
    )
    defaulted = deployment.paper_maker_fee_rate is None or deployment.paper_taker_fee_rate is None
    if defaulted:
        reasons.append(TwinComparisonReason.PAPER_FEE_RATES_DEFAULTED)
    observed: list[Decimal] = []
    counterfactual: list[Decimal] = []
    without_evidence = 0
    for book in live_report.books:
        for fill in _iter_report_fills(book):
            observed.append(Decimal(fill.fee))
            if fill.liquidity is None:
                without_evidence += 1
                continue
            rate = maker if fill.liquidity == "maker" else taker
            counterfactual.append(Decimal(fill.price) * Decimal(fill.quantity) * rate)
    if without_evidence:
        reasons.append(TwinComparisonReason.LIVE_FILLS_WITHOUT_LIQUIDITY_EVIDENCE)
    coverage_incomplete = bool(
        live_report.evidence.unapplied_fill_count or live_report.evidence.orphan_fill_count
    )
    if coverage_incomplete:
        reasons.append(TwinComparisonReason.LIVE_FILL_COVERAGE_INCOMPLETE)
    if not observed:
        return None
    complete = not without_evidence and not coverage_incomplete
    observed_total = _sum_exact(observed)
    counterfactual_total = _sum_exact(counterfactual) if complete else None
    return ExecutionFeeNormalization(
        maker_fee_rate=canonical_decimal(maker),
        taker_fee_rate=canonical_decimal(taker),
        rate_source="documented_defaults" if defaulted else "stored_paper_assumptions",
        observed_live_fees=canonical_decimal(observed_total),
        counterfactual_live_fees_at_paper_rates=None
        if counterfactual_total is None
        else canonical_decimal(counterfactual_total),
        fee_delta=None
        if counterfactual_total is None
        else canonical_decimal(counterfactual_total - observed_total),
        fills_without_liquidity_evidence=without_evidence,
        fill_count=len(observed),
        complete=complete,
    )


def _iter_report_fills(book: ExecutionQualityBook) -> Iterable[ExecutionQualityFill]:
    """Yield raw applied fills exactly once, including partial and over-covering exits."""
    yield from book.recorded_fills


def render_execution_quality_text(report: ExecutionQualityReport) -> str:
    """Render one short human summary of an execution-quality report."""
    lines = [
        f"deployment={report.deployment_id}",
        f"mode={report.mode.value} status={report.status}",
        f"closed_trades={report.totals.closed_trade_count}",
        f"net_pnl={report.totals.net_pnl}",
        f"entry_fees={report.totals.entry_fees} exit_fees={report.totals.exit_fees}",
        f"before_fees_pnl={report.totals.fill_price_pnl_before_fees}",
        f"evidence={'complete' if report.evidence.complete else 'incomplete'}",
    ]
    if report.totals.weighted_slippage_bps is not None:
        lines.append(f"weighted_slippage_bps={report.totals.weighted_slippage_bps}")
    if report.evidence.reasons:
        lines.append("reasons=" + ",".join(reason.value for reason in report.evidence.reasons))
    return "\n".join(lines)


def render_execution_twin_text(comparison: ExecutionTwinComparison) -> str:
    """Render one short human summary of a twin execution comparison."""
    lines = [
        f"comparable={'yes' if comparison.comparable else 'no'}",
        f"paper={comparison.paper.deployment_id} live={comparison.live.deployment_id}",
        f"paper_net_pnl={comparison.paper.net_pnl} live_net_pnl={comparison.live.net_pnl}",
    ]
    if comparison.fee_normalization is not None:
        lines.append(
            "counterfactual_live_fees_at_paper_rates="
            f"{comparison.fee_normalization.counterfactual_live_fees_at_paper_rates}"
        )
    if comparison.reasons:
        lines.append("reasons=" + ",".join(reason.value for reason in comparison.reasons))
    return "\n".join(lines)


__all__ = [
    "EXECUTION_QUALITY_SCHEMA_VERSION",
    "EXECUTION_TWIN_SCHEMA_VERSION",
    "JOURNALED_CLOSE_PAGE_LIMIT",
    "ExecutionFeeNormalization",
    "ExecutionQualityBook",
    "ExecutionQualityEvidence",
    "ExecutionQualityEvidenceReason",
    "ExecutionQualityFill",
    "ExecutionQualityOpenCycle",
    "ExecutionQualityReport",
    "ExecutionQualityRoundTrip",
    "ExecutionQualityTotals",
    "ExecutionTwinComparison",
    "ExecutionTwinSide",
    "JournaledCloseCoverage",
    "JournaledCloseEvidence",
    "JournaledDecisionClose",
    "TwinComparisonReason",
    "TwinOverlapWindow",
    "build_execution_quality_report",
    "build_execution_twin_comparison",
    "load_journaled_close_evidence",
    "positions_by_product",
    "render_execution_quality_text",
    "render_execution_twin_text",
]
