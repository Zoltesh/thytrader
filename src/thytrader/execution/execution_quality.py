"""Read-only live round-trip cost attribution and execution-quality evidence (ADR 0116).

This module folds one deployment's recorded fills into closed round trips and reports,
per trip and in total: fill-price PnL before fees, exact recorded entry and exit fees,
net PnL, and execution slippage against the journaled decision close of the bar that
contains each fill. It never mutates historical fills, never invents missing fees,
liquidity, marks, or closes, and never treats absent evidence as zero. Anything it
cannot prove is disclosed as an evidence reason instead.

The fold mirrors the fill ledger's lot semantics (buys open or add to a long and cover
a short; sells mirror), while sums stay direct: each round trip's numbers are exact
sums of its recorded fills, so they can differ from the ledger's incrementally realized
net by fee-allocation rounding. That difference is disclosed as
``ledger_realized_delta`` exactly like the backtest cost attribution's
``summary_net_pnl_delta``; it is never silently attributed to fees.
"""

from __future__ import annotations

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
from thytrader.market_data.models import parse_candle_interval
from thytrader.research.indicators import canonical_decimal

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from thytrader.execution.models import Deployment, Fill, Order, Position
    from thytrader.execution.twins import DeploymentTwinLink

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
    against the journaled decision close of the bar containing the fill (positive is
    worse than that close) and is null when no journaled close exists.
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
    """A still-open position cycle: recorded entry evidence only, no invented PnL."""

    direction: CycleDirection
    opened_at: datetime
    quantity: QualityDecimalText
    entry_notional: QualityDecimalText
    entry_fees: QualityDecimalText
    entries: tuple[ExecutionQualityFill, ...] = Field(min_length=1)
    position_matches_ledger: bool

    @field_serializer("opened_at", when_used="json")
    def serialize_opened_at(self, value: datetime) -> str:
        """Render the opening instant canonically."""
        return _utc_text(value)


class ExecutionQualityBook(_FrozenQualityModel):
    """One product's closed round trips, open cycle, and exact closed-trade totals."""

    product_id: str
    closed_trade_count: int = Field(ge=0)
    round_trips: tuple[ExecutionQualityRoundTrip, ...] = ()
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
class _RecordedFill:
    """One applied fill plus the liquidity, side, and slippage evidence proven for it."""

    fill: Fill
    order_side: OrderSide
    liquidity: MakerTakerEvidence | None
    slippage_bps: Decimal | None


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
    journaled_closes: Mapping[tuple[str, datetime], Decimal] | None = None,
    decision_coverage: tuple[datetime, datetime] | None = None,
    decision_rows_fetched: int | None = None,
    journaled_entry_signals: int | None = None,
    ledger: DeploymentLedger | None = None,
    extra_reasons: Sequence[ExecutionQualityEvidenceReason] = (),
) -> ExecutionQualityReport:
    """Fold one deployment snapshot into read-only execution-quality evidence.

    Args:
        snapshot: The full deployment snapshot (deployment, orders, fills, positions).
        journaled_closes: Optional ``(product_id, bar_starts_at)`` → close-price map
            built from the per-bar decision journal; missing entries are disclosed,
            never defaulted.
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
        mode=deployment.mode,
        status=deployment.status.value,
        strategy_fingerprint=deployment.strategy_fingerprint,
        timeframe=deployment.timeframe,
        books=books,
        totals=totals,
        evidence=evidence,
    )


def _coverage_response(
    closes: Mapping[tuple[str, datetime], Decimal],
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
    closes: Mapping[tuple[str, datetime], Decimal],
    duration: timedelta | None,
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
        slippage: Decimal | None = None
        if duration is not None:
            bar = _bar_start(fill.filled_at, duration)
            close = closes.get((product_id, bar))
            if close is None:
                reasons.append(ExecutionQualityEvidenceReason.FILL_WITHOUT_JOURNALED_CLOSE)
            else:
                slippage = _signed_slippage_bps(fill, order.side, close)
        _apply_fill(
            book,
            _RecordedFill(
                fill=fill,
                order_side=order.side,
                liquidity=liquidity,
                slippage_bps=slippage,
            ),
            reasons,
        )
    return [books[key] for key in sorted(books)]


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
    )


def _book_response(fold: _BookFold, position: Position | None) -> ExecutionQualityBook:
    """Freeze one product book with its closed-trade totals."""
    trips = tuple(_trip_response(cycle, closed_at) for cycle, closed_at in fold.trips)
    return ExecutionQualityBook(
        product_id=fold.product_id,
        closed_trade_count=len(trips),
        round_trips=trips,
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
    journaled = sum(trip.slippage_fills_journaled for trip in trips)
    total_fills = sum(trip.slippage_fills_total for trip in trips)
    weighted = _weighted_slippage(
        [
            (
                None if fill.slippage_bps is None else Decimal(fill.slippage_bps),
                Decimal(fill.quantity),
            )
            for trip in trips
            for fill in (*trip.entries, *trip.exits)
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

    closes: dict[tuple[str, datetime], Decimal]
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
    stopping early once rows at least as old as the earliest applied fill's bar exist.
    A missing journal yields no closes and no coverage, never a default.
    """
    if decision_storage_label(store) == "unavailable":
        return JournaledCloseEvidence({}, None, 0, False)
    deployment = snapshot.deployment
    duration = (
        parse_candle_interval(deployment.timeframe).duration if deployment.timeframe else None
    )
    orders = {order.id: order for order in snapshot.orders}
    earliest = _earliest_fill_bars(snapshot, deployment, orders, duration)
    products = sorted(earliest)
    closes: dict[tuple[str, datetime], Decimal] = {}
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
                    closes[(product_id, decision.bar_starts_at)] = Decimal(decision.close_price)
            cursor = page.next_cursor
            if cursor is None:
                exhausted = True
                break
            oldest_seen = page.decisions[-1].bar_starts_at if page.decisions else None
            if oldest_seen is not None and oldest_seen <= earliest[product_id]:
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
    duration: timedelta | None,
) -> dict[str, datetime]:
    """Map each product with fills to the bar of its earliest applied fill."""
    earliest: dict[str, datetime] = {}
    if duration is None:
        return {
            resolved_product_id(order.product_id, deployment): datetime.max.replace(tzinfo=UTC)
            for fill in snapshot.fills
            if fill.economics_applied_at is not None
            and (order := orders.get(fill.order_id)) is not None
        }
    for fill in snapshot.fills:
        order = orders.get(fill.order_id)
        if order is None or fill.economics_applied_at is None:
            continue
        product_id = resolved_product_id(order.product_id, deployment)
        bar = _bar_start(fill.filled_at, duration)
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

    ``counterfactual_live_fees_at_paper_rates`` re-prices only the live fills whose
    recorded liquidity proves which paper rate applies; fills without that evidence
    are counted, not guessed.
    """

    maker_fee_rate: QualityDecimalText
    taker_fee_rate: QualityDecimalText
    rate_source: Literal["stored_paper_assumptions", "documented_defaults"]
    observed_live_fees: QualityDecimalText
    counterfactual_live_fees_at_paper_rates: QualityDecimalText
    fee_delta: QualityDecimalText
    fills_without_liquidity_evidence: int = Field(ge=0)


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
) -> ExecutionTwinComparison:
    """Compare two explicitly linked books on recorded execution evidence only.

    Comparability requires an overlapping fill window and complete evidence on both
    sides; anything else is disclosed as a reason, never silently smoothed over. The
    fee normalization is counterfactual and separate: no realized field is rewritten.

    Raises:
        ValueError: When the link's members do not match the supplied reports.
    """
    reasons: list[TwinComparisonReason] = []
    if (
        link.paper_deployment_id != paper_report.deployment_id
        or link.live_deployment_id != live_report.deployment_id
    ):
        raise ValueError("Twin link members do not match the supplied reports.")
    paper_deployment = paper_snapshot.deployment
    live_deployment = live_snapshot.deployment
    if paper_deployment.strategy_fingerprint != live_deployment.strategy_fingerprint:
        reasons.append(TwinComparisonReason.SNAPSHOT_FINGERPRINTS_DIFFER)
    overlap = _twin_overlap(paper_report, live_report)
    if overlap is None:
        reasons.append(TwinComparisonReason.NO_OVERLAPPING_FILLS)
    if not paper_report.evidence.complete:
        reasons.append(TwinComparisonReason.INCOMPLETE_PAPER_EVIDENCE)
    if not live_report.evidence.complete:
        reasons.append(TwinComparisonReason.INCOMPLETE_LIVE_EVIDENCE)
    paper_side = _twin_side(paper_report)
    live_side = _twin_side(live_report)
    if overlap is not None and paper_side.entry_fill_count != live_side.entry_fill_count:
        reasons.append(TwinComparisonReason.ENTRY_FILL_COUNT_DIVERGENCE)
    normalization = _fee_normalization(paper_snapshot, live_report, overlap, reasons)
    blocking = {
        TwinComparisonReason.NO_OVERLAPPING_FILLS,
        TwinComparisonReason.INCOMPLETE_PAPER_EVIDENCE,
        TwinComparisonReason.INCOMPLETE_LIVE_EVIDENCE,
    }
    return ExecutionTwinComparison(
        paper=paper_side,
        live=live_side,
        strategy_fingerprint_paper=paper_deployment.strategy_fingerprint,
        strategy_fingerprint_live=live_deployment.strategy_fingerprint,
        product_id=live_deployment.product_id,
        timeframe=live_deployment.timeframe,
        overlap=overlap,
        comparable=not any(reason in blocking for reason in reasons),
        reasons=tuple(dict.fromkeys(reasons)),
        fee_normalization=normalization,
    )


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
    overlap: TwinOverlapWindow | None,
    reasons: list[TwinComparisonReason],
) -> ExecutionFeeNormalization | None:
    """Re-price recorded live fills at the paper book's fee assumptions.

    Realized live fees stay untouched; this is a disclosed counterfactual over the
    fills whose liquidity the records prove, using each fill's own quote notional.
    """
    deployment = paper_snapshot.deployment
    maker, taker = effective_paper_fee_rates(
        deployment.paper_maker_fee_rate, deployment.paper_taker_fee_rate
    )
    defaulted = deployment.paper_maker_fee_rate is None
    if defaulted:
        reasons.append(TwinComparisonReason.PAPER_FEE_RATES_DEFAULTED)
    observed: list[Decimal] = []
    counterfactual: list[Decimal] = []
    without_evidence = 0
    for book in live_report.books:
        for fill in _iter_report_fills(book):
            if overlap is not None and not (
                overlap.first_fill_at <= fill.filled_at <= overlap.last_fill_at
            ):
                continue
            observed.append(Decimal(fill.fee))
            if fill.liquidity is None:
                without_evidence += 1
                continue
            rate = maker if fill.liquidity == "maker" else taker
            counterfactual.append(Decimal(fill.price) * Decimal(fill.quantity) * rate)
    if without_evidence:
        reasons.append(TwinComparisonReason.LIVE_FILLS_WITHOUT_LIQUIDITY_EVIDENCE)
    observed_total = _sum_exact(observed)
    counterfactual_total = _sum_exact(counterfactual)
    return ExecutionFeeNormalization(
        maker_fee_rate=canonical_decimal(maker),
        taker_fee_rate=canonical_decimal(taker),
        rate_source="documented_defaults" if defaulted else "stored_paper_assumptions",
        observed_live_fees=canonical_decimal(observed_total),
        counterfactual_live_fees_at_paper_rates=canonical_decimal(counterfactual_total),
        fee_delta=canonical_decimal(counterfactual_total - observed_total),
        fills_without_liquidity_evidence=without_evidence,
    )


def _iter_report_fills(book: ExecutionQualityBook) -> Iterable[ExecutionQualityFill]:
    """Yield every fill a book's evidence carries, entries and exits alike."""
    for trip in book.round_trips:
        yield from trip.entries
        yield from trip.exits
    if book.open_cycle is not None:
        yield from book.open_cycle.entries


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
    "TwinComparisonReason",
    "TwinOverlapWindow",
    "build_execution_quality_report",
    "build_execution_twin_comparison",
    "load_journaled_close_evidence",
    "positions_by_product",
    "render_execution_quality_text",
    "render_execution_twin_text",
]
