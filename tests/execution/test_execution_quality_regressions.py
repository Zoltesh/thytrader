"""Lead-review regressions: causal benchmarks, equal populations, and unknown costs."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4, uuid7

import pytest

from tests.execution.test_execution_quality import _deployment, _fill, _order, _position
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import BarDecision, DecisionOutcome
from thytrader.execution.execution_quality import (
    ExecutionQualityReport,
    ExecutionTwinComparison,
    JournaledDecisionClose,
    TwinComparisonReason,
    build_execution_quality_report,
    build_execution_twin_comparison,
    load_journaled_close_evidence,
)
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    IntentPurpose,
    OrderIntent,
    OrderKind,
    OrderSide,
)
from thytrader.execution.twins import DeploymentTwinLink
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import strategy_fingerprint
from thytrader.strategies.snapshots import StrategySnapshot

START = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _book(
    deployment: Deployment,
    *,
    start_hour: int = 1,
    exit_hour: int = 2,
    exit_kind: OrderKind = OrderKind.MARKETABLE,
    exit_quantity: str = "1",
    partial: bool = False,
) -> tuple[DeploymentSnapshot, dict[tuple[str, datetime], JournaledDecisionClose]]:
    """Real test intents use completed bars before both submission and execution."""
    entered = START + HOUR * start_hour
    exited = START + HOUR * exit_hour
    buy = replace(
        _order(deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT),
        created_at=entered,
    )
    sell = replace(
        _order(deployment.id, OrderSide.SELL, exit_kind, quantity=exit_quantity),
        created_at=exited,
    )
    fills = (
        _fill(buy, at=entered, price="100", fee="0.1"),
        _fill(sell, at=exited, price="110", fee=str(Decimal(exit_quantity) * Decimal("0.11"))),
    )
    intents = tuple(
        OrderIntent(
            id=order.intent_id,
            deployment_id=deployment.id,
            client_order_id=order.client_order_id,
            purpose=purpose,
            side=order.side,
            kind=order.kind,
            quantity=order.quantity,
            created_at=order.created_at,
            candle_starts_at=order.created_at - HOUR,
            product_id=deployment.product_id,
        )
        for order, purpose in ((buy, IntentPurpose.ENTRY), (sell, IntentPurpose.TIME_EXIT))
    )
    positions = (
        (
            _position(
                deployment, quantity=str(Decimal(1) - Decimal(exit_quantity)), entry_price="100"
            ),
        )
        if partial
        else ()
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        orders=(buy, sell),
        fills=fills,
        intents=intents,
        positions=positions,
        position=positions[0] if positions else None,
    )
    closes = {
        (deployment.product_id, entered - HOUR): JournaledDecisionClose(Decimal("100"), entered),
        (deployment.product_id, exited - HOUR): JournaledDecisionClose(Decimal("110"), exited),
    }
    return snapshot, closes


def _report(
    snapshot: DeploymentSnapshot,
    closes: dict[tuple[str, datetime], JournaledDecisionClose],
) -> ExecutionQualityReport:
    """Build only from recorded test facts, with the intent bar's actual completion."""
    return build_execution_quality_report(snapshot, journaled_closes=closes)


def _compare(
    paper: DeploymentSnapshot,
    paper_report: ExecutionQualityReport,
    live: DeploymentSnapshot,
    live_report: ExecutionQualityReport,
    *,
    proof: tuple[StrategySnapshot, StrategySnapshot] | None = None,
) -> ExecutionTwinComparison:
    """Use the actual snapshots that generated the reports, not empty stand-ins."""
    return build_execution_twin_comparison(
        link=DeploymentTwinLink(paper.deployment.id, live.deployment.id, START),
        paper_snapshot=paper,
        paper_report=paper_report,
        live_snapshot=live,
        live_report=live_report,
        strategy_snapshots=proof,
    )


def test_unknown_liquidity_fees_do_not_become_zero_counterfactual_costs() -> None:
    """Observed total includes an unknown exit; counterfactual and delta are unavailable."""
    paper, pc = _book(_deployment(uuid4()))
    live, lc = _book(
        _deployment(uuid4(), mode=DeploymentMode.LIVE), exit_kind=OrderKind.TRIGGER_BRACKET
    )
    before = live.fills
    comparison = _compare(paper, _report(paper, pc), live, _report(live, lc))
    normalization = comparison.fee_normalization
    assert normalization is not None
    assert normalization.observed_live_fees == "0.21"
    assert normalization.fill_count == 2
    assert normalization.fills_without_liquidity_evidence == 1
    assert normalization.counterfactual_live_fees_at_paper_rates is None
    assert normalization.fee_delta is None
    assert not normalization.complete
    assert not comparison.comparable
    assert live.fills == before


@pytest.mark.parametrize(
    "quantity,partial,counterfactual", [("0.4", True, "0.188"), ("1", False, "0.32")]
)
def test_partial_exits_are_visible_and_normalized_exactly_once(
    quantity: str,
    partial: bool,
    counterfactual: str,
) -> None:
    """Open-cycle partial exits count once now and still once when the cycle closes."""
    paper, pc = _book(_deployment(uuid4()))
    live, lc = _book(
        _deployment(uuid4(), mode=DeploymentMode.LIVE),
        exit_quantity=quantity,
        partial=partial,
    )
    report = _report(live, lc)
    book = report.books[0]
    assert len(book.recorded_fills) == len(live.fills) == 2
    assert {f.fill_id for f in book.recorded_fills} == {f.id for f in live.fills}
    if partial:
        assert book.open_cycle is not None
        assert len(book.open_cycle.exits) == 1
        assert book.open_cycle.exits[0].fill_id == live.fills[1].id
        assert report.totals.net_pnl == "0"  # Closed-cycle scope, not missing exit fees.
        assert report.totals.ledger_realized_delta == "3.916"  # Realized partial-exit scope.
    comparison = _compare(paper, _report(paper, pc), live, report)
    normalization = comparison.fee_normalization
    assert normalization is not None and normalization.complete
    assert normalization.fill_count == 2
    assert normalization.observed_live_fees == str(
        Decimal("0.1") + Decimal(quantity) * Decimal("0.11")
    )
    assert normalization.counterfactual_live_fees_at_paper_rates == counterfactual
    assert report.totals.slippage_fills_total == 2


def test_over_cover_normalization_uses_original_fill_not_clamped_projection() -> None:
    """Cycle accounting may clamp inventory; repricing must preserve raw recorded exposure."""
    paper, pc = _book(_deployment(uuid4()))
    live, lc = _book(_deployment(uuid4(), mode=DeploymentMode.LIVE), exit_quantity="2")
    report = _report(live, lc)
    assert report.books[0].round_trips[0].exits[0].quantity == "1"
    assert report.books[0].recorded_fills[1].quantity == "2"
    normalization = _compare(paper, _report(paper, pc), live, report).fee_normalization
    assert normalization is not None
    assert normalization.counterfactual_live_fees_at_paper_rates == "0.54"
    assert live.fills[1].quantity == Decimal(2)


def test_unequal_lifetime_windows_are_context_only_despite_overlap() -> None:
    """Equal counts but intersecting, unequal histories cannot support lifetime attribution."""
    paper, pc = _book(_deployment(uuid4()), start_hour=1, exit_hour=2)
    live, lc = _book(_deployment(uuid4(), mode=DeploymentMode.LIVE), start_hour=2, exit_hour=3)
    pr, lr = _report(paper, pc), _report(live, lc)
    assert pr.evidence.complete and lr.evidence.complete
    comparison = _compare(paper, pr, live, lr)
    assert comparison.overlap is not None
    assert not comparison.comparable
    assert comparison.population == "recorded_fill_lifetime"
    assert comparison.summaries_context_only
    assert TwinComparisonReason.LIFETIME_WINDOWS_DIFFER in comparison.reasons
    normalization = comparison.fee_normalization
    assert normalization is not None
    assert normalization.population == "live_applied_fill_lifetime"
    assert normalization.fill_count == 2  # Not just the one fill in the overlap.
    assert normalization.observed_live_fees == "0.21"
    assert normalization.counterfactual_live_fees_at_paper_rates == "0.32"


def test_same_bounds_but_different_quantities_cannot_compare() -> None:
    """Same timestamps and counts do not prove equal product/side/quantity populations."""
    paper, pc = _book(_deployment(uuid4()))
    live, lc = _book(_deployment(uuid4(), mode=DeploymentMode.LIVE))
    live = replace(
        live, fills=tuple(replace(f, quantity=Decimal(2), fee=f.fee * 2) for f in live.fills)
    )
    comparison = _compare(paper, _report(paper, pc), live, _report(live, lc))
    assert comparison.overlap is not None
    assert not comparison.comparable
    assert TwinComparisonReason.FILL_POPULATIONS_DIFFER in comparison.reasons


def test_no_overlap_does_not_silently_change_fee_normalization_population() -> None:
    """Lifetime normalization retains the same scope whether or not twins overlap."""
    paper, pc = _book(_deployment(uuid4()))
    live, lc = _book(_deployment(uuid4(), mode=DeploymentMode.LIVE))
    normal = _compare(paper, _report(paper, pc), live, _report(live, lc))
    older, oc = _book(paper.deployment, start_hour=-5, exit_hour=-4)
    disjoint = _compare(older, _report(older, oc), live, _report(live, lc))
    assert disjoint.overlap is None and not disjoint.comparable
    assert normal.fee_normalization == disjoint.fee_normalization


@pytest.mark.parametrize("different_rules", [False, True])
def test_different_fingerprints_need_verified_pinned_trading_rules(different_rules: bool) -> None:
    """Annotations/identity clones are compatible only with proof; different rules block."""
    definition = create_template_strategy(product_id="BTC-USD")
    clone = definition.model_copy(update={"strategy_id": uuid7(), "name": "Paper clone"})
    if different_rules:
        clone = clone.model_copy(
            update={
                "execution": clone.execution.model_copy(
                    update={
                        "max_entry_wait_bars": clone.execution.max_entry_wait_bars + 1,
                    }
                )
            }
        )
    proof = (
        StrategySnapshot(strategy_fingerprint(definition), definition),
        StrategySnapshot(strategy_fingerprint(clone), clone),
    )
    paper, pc = _book(
        replace(_deployment(uuid4()), strategy_fingerprint=proof[0].strategy_fingerprint)
    )
    live, lc = _book(
        replace(
            _deployment(uuid4(), mode=DeploymentMode.LIVE),
            strategy_fingerprint=proof[1].strategy_fingerprint,
        )
    )
    pr, lr = _report(paper, pc), _report(live, lc)
    assert pr.evidence.complete and lr.evidence.complete
    unproven = _compare(paper, pr, live, lr)
    assert not unproven.comparable
    assert TwinComparisonReason.TRADING_RULES_UNVERIFIED in unproven.reasons
    proven = _compare(paper, pr, live, lr, proof=proof)
    assert proven.comparable is not different_rules
    if different_rules:
        assert TwinComparisonReason.TRADING_RULES_INCOMPATIBLE in proven.reasons
    else:
        assert not proven.summaries_context_only
        assert proven.paper.entry_fees == proven.live.entry_fees  # Observed, not rewritten.
    bad_proof = (proof[0], StrategySnapshot(proof[1].strategy_fingerprint, definition))
    assert not _compare(paper, pr, live, lr, proof=bad_proof).comparable


@pytest.mark.parametrize(
    "invalid",
    ["mode", "member", "report_mode", "report_market", "report_clock", "report_snapshot", "fills"],
)
def test_snapshots_and_reports_must_match_link_members(invalid: str) -> None:
    """A saved link cannot make mismatched source rows or evidence trustworthy."""
    paper, pc = _book(_deployment(uuid4()))
    live, lc = _book(_deployment(uuid4(), mode=DeploymentMode.LIVE))
    pr, lr = _report(paper, pc), _report(live, lc)
    match invalid:
        case "mode":
            live = replace(live, deployment=replace(live.deployment, mode=DeploymentMode.PAPER))
        case "member":
            live = replace(live, deployment=replace(live.deployment, id=uuid4()))
        case "report_mode":
            lr = lr.model_copy(update={"mode": DeploymentMode.PAPER})
        case "report_market":
            lr = lr.model_copy(update={"product_id": "ETH-USD"})
        case "report_clock":
            lr = lr.model_copy(update={"timeframe": "5m"})
        case "report_snapshot":
            lr = lr.model_copy(update={"strategy_fingerprint": "sha256:" + "f" * 64})
        case "fills":
            live = replace(live, fills=())
        case _:
            raise AssertionError(invalid)
    with pytest.raises(ValueError, match=r"(do not match|does not match)"):
        _compare(paper, pr, live, lr)


@pytest.mark.parametrize("invalid", ["market", "clock", "kind"])
def test_twins_with_incompatible_market_clock_or_kind_are_context_only(invalid: str) -> None:
    """Even a matching strategy fingerprint cannot waive deployment compatibility."""
    paper, pc = _book(_deployment(uuid4()))
    live_deployment = _deployment(uuid4(), mode=DeploymentMode.LIVE)
    match invalid:
        case "market":
            live_deployment = replace(live_deployment, product_id="ETH-USD")
        case "clock":
            live_deployment = replace(live_deployment, timeframe="5m")
        case "kind":
            live_deployment = replace(live_deployment, kind=DeploymentKind.DISCRETIONARY)
        case _:
            raise AssertionError(invalid)
    live, lc = _book(live_deployment)
    comparison = _compare(paper, _report(paper, pc), live, _report(live, lc))
    assert not comparison.comparable
    assert TwinComparisonReason.TRADING_RULES_INCOMPATIBLE in comparison.reasons


def test_slippage_uses_intent_close_not_future_fill_bar_close() -> None:
    """A delayed fill uses the original completed intent bar, with full provenance."""
    snapshot, closes = _book(_deployment(uuid4()))
    entry = snapshot.fills[0]
    snapshot = replace(snapshot, fills=(replace(entry, filled_at=START + HOUR * 5),))
    closes[("BTC-USD", START + HOUR * 5)] = JournaledDecisionClose(Decimal("200"), START + HOUR * 6)
    record = _report(snapshot, closes).books[0].recorded_fills[0]
    assert record.slippage_bps == "0"
    assert record.reference_price == "100"
    assert record.reference_intent_id == snapshot.intents[0].id
    assert record.reference_bar_starts_at == START
    assert record.reference_bar_closes_at == snapshot.intents[0].created_at
    assert record.reference_bar_closes_at is not None
    assert record.reference_bar_closes_at <= snapshot.orders[0].created_at <= record.filled_at
    only_future = {("BTC-USD", START + HOUR * 5): closes[("BTC-USD", START + HOUR * 5)]}
    missing = _report(snapshot, only_future).books[0].recorded_fills[0]
    assert missing.slippage_bps is None and missing.reference_price is None


@pytest.mark.parametrize(
    "invalid",
    ["future_bar", "late_order", "late_intent", "early_fill", "missing_intent", "attached_child"],
)
def test_missing_or_noncausal_intent_references_are_null(invalid: str) -> None:
    """Future closes or absent decision linkage never become causal zero-slippage evidence."""
    snapshot, closes = _book(_deployment(uuid4()))
    intent = snapshot.intents[0]
    match invalid:
        case "future_bar":
            snapshot = replace(
                snapshot,
                intents=(replace(intent, candle_starts_at=START + HOUR), *snapshot.intents[1:]),
            )
        case "late_order":
            snapshot = replace(
                snapshot,
                orders=(replace(snapshot.orders[0], created_at=START), *snapshot.orders[1:]),
            )
        case "late_intent":
            snapshot = replace(
                snapshot,
                intents=(replace(intent, created_at=START + HOUR * 8), *snapshot.intents[1:]),
            )
        case "early_fill":
            snapshot = replace(
                snapshot, fills=(replace(snapshot.fills[0], filled_at=START), *snapshot.fills[1:])
            )
        case "missing_intent":
            snapshot = replace(snapshot, intents=())
        case "attached_child":
            snapshot = replace(
                snapshot,
                orders=(replace(snapshot.orders[0], parent_order_id=uuid4()), *snapshot.orders[1:]),
            )
        case _:
            raise AssertionError(invalid)
    report = _report(snapshot, closes)
    record = next(
        f for b in report.books for f in b.recorded_fills if f.fill_id == snapshot.fills[0].id
    )
    assert record.slippage_bps is None
    assert record.reference_price is None and record.reference_intent_id is None
    assert record.reference_bar_starts_at is None and record.reference_bar_closes_at is None
    assert not report.evidence.complete


@pytest.mark.anyio
async def test_journal_paging_reaches_original_intent_bar_for_delayed_fills() -> None:
    """A fill ten bars later must not cause paging to stop at its recent fill bar."""
    snapshot, _closes = _book(_deployment(uuid4()))
    snapshot = replace(snapshot, fills=(replace(snapshot.fills[0], filled_at=START + HOUR * 10),))
    journal = InMemoryDecisionJournalStore()
    for hour in range(12):
        bar = START + HOUR * hour
        await journal.upsert(
            BarDecision(
                deployment_id=snapshot.deployment.id,
                product_id="BTC-USD",
                timeframe="1h",
                mode=DeploymentMode.PAPER,
                bar_starts_at=bar,
                bar_closes_at=bar + HOUR,
                evaluated_at=bar + HOUR,
                outcome=DecisionOutcome.NO_SIGNAL,
                reason_code="NO_SIGNAL",
                summary="No signal.",
                close_price=str(100 + hour),
            )
        )
    evidence = await load_journaled_close_evidence(
        journal, deployment_id=snapshot.deployment.id, snapshot=snapshot, page_limit=2, max_pages=10
    )
    assert ("BTC-USD", START) in evidence.closes
    assert not evidence.coverage_limited
    record = _report(snapshot, evidence.closes).books[0].recorded_fills[0]
    assert record.reference_price == "100"
    capped = await load_journaled_close_evidence(
        journal, deployment_id=snapshot.deployment.id, snapshot=snapshot, page_limit=2, max_pages=2
    )
    assert capped.coverage_limited
    assert _report(snapshot, capped.closes).books[0].recorded_fills[0].slippage_bps is None


def test_excluded_fill_coverage_makes_complete_counterfactual_unavailable() -> None:
    """Applied fees are not a complete cost population while other fills are pending."""
    paper, pc = _book(_deployment(uuid4()))
    live, lc = _book(_deployment(uuid4(), mode=DeploymentMode.LIVE))
    live = replace(live, fills=(live.fills[0], replace(live.fills[1], economics_applied_at=None)))
    comparison = _compare(paper, _report(paper, pc), live, _report(live, lc))
    normalization = comparison.fee_normalization
    assert normalization is not None
    assert normalization.observed_live_fees == "0.1"
    assert normalization.fill_count == 1
    assert normalization.counterfactual_live_fees_at_paper_rates is None
    assert normalization.fee_delta is None
    assert not normalization.complete
    assert TwinComparisonReason.LIVE_FILL_COVERAGE_INCOMPLETE in comparison.reasons


def test_equal_unfilled_counts_but_different_decision_orders_cannot_compare() -> None:
    """Two one-order unmatched populations may refer to different decisions and are not equal."""
    paper, pc = _book(_deployment(uuid4()))
    live, lc = _book(_deployment(uuid4(), mode=DeploymentMode.LIVE))
    extended = []
    for snapshot, hour in ((paper, 5), (live, 7)):
        order = replace(snapshot.orders[0], id=uuid4(), intent_id=uuid4())
        intent = replace(
            snapshot.intents[0], id=order.intent_id, candle_starts_at=START + HOUR * hour
        )
        extended.append(
            replace(snapshot, orders=(*snapshot.orders, order), intents=(*snapshot.intents, intent))
        )
    paper, live = extended
    pr, lr = _report(paper, pc), _report(live, lc)
    assert pr.evidence.complete and lr.evidence.complete
    assert pr.totals.unfilled_entry_orders == lr.totals.unfilled_entry_orders == 1
    comparison = _compare(paper, pr, live, lr)
    assert not comparison.comparable
    assert TwinComparisonReason.ORDER_POPULATIONS_DIFFER in comparison.reasons
