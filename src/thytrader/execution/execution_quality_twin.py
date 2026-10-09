"""Paper/live twin comparison over recorded execution-quality evidence (ADR 0116).

Compares two explicitly linked books on their recorded fills only: rule equivalence
via ADR 0105 snapshots, lifetime fill and order populations, overlapping fill windows,
and a disclosed counterfactual that re-prices live fills at the paper fee assumptions.
Anything it cannot prove is a comparison reason, never a silent alignment.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.decimal_text import canonical_decimal
from thytrader.execution.execution_quality_common import (
    _liquidity_evidence,
    _partition_fills,
    _sum_exact,
)
from thytrader.execution.execution_quality_models import (
    ExecutionFeeNormalization,
    ExecutionQualityBook,
    ExecutionQualityFill,
    ExecutionQualityReport,
    ExecutionTwinComparison,
    ExecutionTwinSide,
    TwinComparisonReason,
    TwinOverlapWindow,
)
from thytrader.trading.ledger import effective_paper_fee_rates
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    IntentPurpose,
    OrderSide,
    resolved_product_id,
)
from thytrader.trading.twins import TwinValidationError, comparable_twins

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime
    from uuid import UUID

    from thytrader.strategies.snapshots import StrategySnapshot
    from thytrader.trading.models import Deployment, Fill, Order
    from thytrader.trading.twins import DeploymentTwinLink


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
    entire applied-fill lifetime. Unknown liquidity makes the total unavailable. An
    adoption fill (ADR 0124) was never executed and has no fee to re-price, so it is
    left out of both sums and of ``fill_count``.
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
            if fill.adopted:
                continue
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
