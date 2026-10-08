"""Round-trip cost attribution and execution-quality evidence tests (ADR 0116)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import BarDecision, DecisionOutcome
from thytrader.execution.execution_quality import (
    ExecutionQualityEvidenceReason,
    JournaledDecisionClose,
    TwinComparisonReason,
    build_execution_quality_report,
)
from thytrader.execution.execution_quality_journal import load_journaled_close_evidence
from thytrader.execution.execution_quality_twin import build_execution_twin_comparison
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
)
from thytrader.trading.twins import DeploymentTwinLink

START = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)
FINGERPRINT = "sha256:" + "a" * 64


def _at(hour: int, minute: int = 0) -> datetime:
    """One UTC instant on the test day."""
    return START + HOUR * hour + timedelta(minutes=minute)


def _deployment(
    deployment_id: UUID,
    *,
    mode: DeploymentMode = DeploymentMode.PAPER,
    product_id: str = "BTC-USD",
    timeframe: str | None = "1h",
    maker_rate: str | None = "0.001",
    taker_rate: str | None = "0.002",
) -> Deployment:
    """One strategy deployment row."""
    return Deployment(
        id=deployment_id,
        strategy_fingerprint=FINGERPRINT,
        strategy_id=uuid4(),
        product_id=product_id,
        mode=mode,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.FLAT,
        created_at=START,
        updated_at=START,
        timeframe=timeframe,
        paper_maker_fee_rate=None if maker_rate is None else Decimal(maker_rate),
        paper_taker_fee_rate=None if taker_rate is None else Decimal(taker_rate),
    )


def _order(
    deployment_id: UUID,
    side: OrderSide,
    kind: OrderKind,
    *,
    price: str | None = None,
    quantity: str = "1",
    product_id: str = "",
) -> Order:
    """One venue-visible order derived from a persisted intent."""
    return Order(
        id=uuid4(),
        deployment_id=deployment_id,
        intent_id=uuid4(),
        client_order_id=f"c{uuid4().hex[:8]}",
        side=side,
        kind=kind,
        quantity=Decimal(quantity),
        status=OrderStatus.FILLED,
        created_at=START,
        updated_at=START,
        price=None if price is None else Decimal(price),
        product_id=product_id,
    )


def _fill(
    order: Order,
    *,
    at: datetime,
    price: str,
    quantity: str | None = None,
    fee: str,
    applied: bool = True,
) -> Fill:
    """One exact recorded fill."""
    return Fill(
        id=uuid4(),
        deployment_id=order.deployment_id,
        order_id=order.id,
        venue_fill_id=f"v{uuid4().hex[:12]}",
        price=Decimal(price),
        quantity=Decimal(quantity) if quantity is not None else order.quantity,
        fee=Decimal(fee),
        filled_at=at,
        economics_applied_at=at if applied else None,
    )


def _snapshot(
    deployment: Deployment,
    orders: tuple[Order, ...] = (),
    fills: tuple[Fill, ...] = (),
    positions: tuple[Position, ...] = (),
) -> DeploymentSnapshot:
    """Fixture with orders submitted after the preceding completed decision bar.

    Older tests omitted intents and incorrectly benchmarked a future fill-bar close.
    Model real causal intent/order timestamps here; regression tests separately cover
    missing, late, and future references without this fixture convenience.
    """
    timed_orders = []
    intents = []
    for order in orders:
        instant = min(
            (fill.filled_at for fill in fills if fill.order_id == order.id), default=START
        )
        decision_bar = instant.replace(minute=0, second=0, microsecond=0) - HOUR
        timed_orders.append(replace(order, created_at=instant))
        intents.append(
            OrderIntent(
                id=order.intent_id,
                deployment_id=deployment.id,
                client_order_id=order.client_order_id,
                purpose=IntentPurpose.ENTRY
                if order.side is OrderSide.BUY
                else IntentPurpose.TIME_EXIT,
                side=order.side,
                kind=order.kind,
                quantity=order.quantity,
                created_at=instant,
                candle_starts_at=decision_bar,
                product_id=order.product_id,
            )
        )
    return DeploymentSnapshot(
        deployment=deployment,
        orders=tuple(timed_orders),
        intents=tuple(intents),
        fills=fills,
        positions=positions,
        position=positions[0] if positions else None,
    )


def _position(
    deployment: Deployment,
    *,
    quantity: str,
    entry_price: str,
    side: PositionSide = PositionSide.LONG,
    entered_bar: datetime = START,
) -> Position:
    """One product book row."""
    return Position(
        deployment_id=deployment.id,
        quantity=Decimal(quantity),
        entry_price=Decimal(entry_price),
        stop_price=Decimal("1"),
        target_price=None,
        entered_bar=entered_bar,
        updated_at=START,
        side=side,
        product_id=deployment.product_id,
    )


def _closes(
    *completions: datetime, close: str = "101"
) -> dict[tuple[str, datetime], JournaledDecisionClose]:
    """Journaled prior-bar closes completed at each supplied boundary, not future closes."""
    return {
        ("BTC-USD", completed - HOUR): JournaledDecisionClose(Decimal(close), completed)
        for completed in completions
    }


def test_long_round_trip_reports_exact_fees_net_and_slippage() -> None:
    """One buy and one sell close one trip with exact recorded amounts."""
    deployment = _deployment(uuid4())
    buy = _order(deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
    sell = _order(deployment.id, OrderSide.SELL, OrderKind.MARKETABLE, price="110")
    fills = (
        _fill(buy, at=_at(0, 30), price="100", fee="0.1"),
        _fill(sell, at=_at(1), price="110", fee="0.22"),
    )
    report = build_execution_quality_report(
        _snapshot(deployment, (buy, sell), fills),
        journaled_closes=_closes(_at(0), _at(1)),
    )
    assert len(report.books) == 1
    trip = report.books[0].round_trips[0]
    assert trip.direction == "long"
    assert trip.fill_price_pnl_before_fees == "10"
    assert trip.entry_fees == "0.1"
    assert trip.exit_fees == "0.22"
    assert trip.net_pnl == "9.68"
    assert trip.closed_quantity == "1"
    assert trip.slippage_fills_journaled == 2
    assert Decimal(trip.slippage_bps or "0") < 0  # both fills beat the journaled close
    assert report.totals.net_pnl == "9.68"
    assert report.totals.ledger_realized_delta == "0"
    assert report.evidence.complete
    assert report.evidence.reasons == ()


def test_buy_slippage_is_positive_when_worse_than_the_journaled_close() -> None:
    """A buy above the completed intent-bar close reads as positive (worse) slippage."""
    deployment = _deployment(uuid4())
    buy = _order(deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="102")
    fill = _fill(buy, at=_at(0), price="102", fee="0.1")
    report = build_execution_quality_report(
        _snapshot(deployment, (buy,), (fill,)), journaled_closes=_closes(_at(0))
    )
    cycle = report.books[0].open_cycle
    assert cycle is not None
    assert Decimal(cycle.entries[0].slippage_bps or "0") > 0


def test_partial_exits_and_pyramiding_adds_stay_one_exact_round_trip() -> None:
    """Adds and partial exits become multiple fills inside one direct-sum trip."""
    deployment = _deployment(uuid4())
    buy_one = _order(
        deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100", quantity="1"
    )
    buy_two = _order(
        deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="104", quantity="1"
    )
    sell_one = _order(
        deployment.id, OrderSide.SELL, OrderKind.MARKETABLE, price="110", quantity="1"
    )
    sell_two = _order(
        deployment.id, OrderSide.SELL, OrderKind.MARKETABLE, price="105", quantity="1"
    )
    fills = (
        _fill(buy_one, at=_at(0), price="100", fee="0.1"),
        _fill(buy_two, at=_at(1), price="104", fee="0.104"),
        _fill(sell_one, at=_at(2), price="110", fee="0.11"),
        _fill(sell_two, at=_at(3), price="105", fee="0.105"),
    )
    report = build_execution_quality_report(
        _snapshot(deployment, (buy_one, buy_two, sell_one, sell_two), fills),
        journaled_closes=_closes(_at(0), _at(1), _at(2), _at(3)),
    )
    book = report.books[0]
    assert book.closed_trade_count == 1
    trip = book.round_trips[0]
    assert len(trip.entries) == 2
    assert len(trip.exits) == 2
    assert trip.closed_quantity == "2"
    assert trip.fill_price_pnl_before_fees == "11"  # (110 + 105) - (100 + 104)
    assert trip.net_pnl == "10.581"  # 11 - 0.204 - 0.215


def test_short_round_trip_inverts_the_price_sum() -> None:
    """A sell-then-buy cycle reports entry minus exit notional before fees."""
    deployment = _deployment(uuid4())
    sell = _order(deployment.id, OrderSide.SELL, OrderKind.POST_ONLY_LIMIT, price="110")
    cover = _order(deployment.id, OrderSide.BUY, OrderKind.MARKETABLE, price="100")
    fills = (
        _fill(sell, at=_at(0), price="110", fee="0.11"),
        _fill(cover, at=_at(1), price="100", fee="0.1"),
    )
    report = build_execution_quality_report(
        _snapshot(deployment, (sell, cover), fills), journaled_closes=_closes(_at(0), _at(1))
    )
    trip = report.books[0].round_trips[0]
    assert trip.direction == "short"
    assert trip.fill_price_pnl_before_fees == "10"
    assert trip.net_pnl == "9.79"


def test_open_cycle_discloses_entry_evidence_without_inventing_pnl() -> None:
    """A still-open cycle reports fees and quantity only, and flags itself."""
    deployment = _deployment(uuid4())
    buy = _order(deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
    fill = _fill(buy, at=_at(0), price="100", fee="0.1")
    position = _position(deployment, quantity="1", entry_price="100")
    report = build_execution_quality_report(
        _snapshot(deployment, (buy,), (fill,), (position,)),
        journaled_closes=_closes(_at(0)),
    )
    book = report.books[0]
    assert book.closed_trade_count == 0
    assert book.open_cycle is not None
    assert book.open_cycle.entry_fees == "0.1"
    assert book.open_cycle.position_matches_ledger
    assert report.evidence.complete is False
    assert ExecutionQualityEvidenceReason.OPEN_CYCLE_PRESENT in report.evidence.reasons


def test_open_cycle_disagreement_with_the_position_row_is_flagged() -> None:
    """A fold that disagrees with the persisted book is disclosed, not trusted."""
    deployment = _deployment(uuid4())
    buy = _order(deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100", quantity="2")
    fill = _fill(buy, at=_at(0), price="100", fee="0.2")
    position = _position(deployment, quantity="1", entry_price="100")
    report = build_execution_quality_report(
        _snapshot(deployment, (buy,), (fill,), (position,)),
        journaled_closes=_closes(_at(0)),
    )
    assert ExecutionQualityEvidenceReason.OPEN_POSITION_MISMATCH in report.evidence.reasons


def test_missing_journaled_close_is_disclosed_never_zeroed() -> None:
    """Fills without a journaled close carry null slippage and an evidence reason."""
    deployment = _deployment(uuid4())
    buy = _order(deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
    sell = _order(deployment.id, OrderSide.SELL, OrderKind.MARKETABLE, price="110")
    fills = (
        _fill(buy, at=_at(0), price="100", fee="0.1"),
        _fill(sell, at=_at(1), price="110", fee="0.11"),
    )
    report = build_execution_quality_report(
        _snapshot(deployment, (buy, sell), fills), journaled_closes=_closes(_at(0))
    )
    trip = report.books[0].round_trips[0]
    assert trip.entries[0].slippage_bps is not None
    assert trip.exits[0].slippage_bps is None
    assert trip.slippage_fills_journaled == 1
    assert trip.slippage_fills_total == 2
    assert ExecutionQualityEvidenceReason.FILL_WITHOUT_JOURNALED_CLOSE in report.evidence.reasons


def test_liquidity_is_reported_only_when_the_order_kind_records_it() -> None:
    """Post-only reads maker, marketable reads taker, venue brackets read unknown."""
    deployment = _deployment(uuid4())
    maker = _order(deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
    taker = _order(deployment.id, OrderSide.SELL, OrderKind.MARKETABLE, price="110")
    bracket = _order(deployment.id, OrderSide.SELL, OrderKind.TRIGGER_BRACKET, price="111")
    fills = (
        _fill(maker, at=_at(0), price="100", fee="0.1"),
        _fill(taker, at=_at(1), price="110", fee="0.11"),
        _fill(bracket, at=_at(1, 30), price="111", fee="0.111"),
    )
    report = build_execution_quality_report(_snapshot(deployment, (maker, taker, bracket), fills))
    recorded = [
        fill
        for book in report.books
        for trip in book.round_trips
        for fill in (*trip.entries, *trip.exits)
    ]
    for book in report.books:
        if book.open_cycle is not None:
            recorded.extend(book.open_cycle.entries)
    assert {fill.liquidity for fill in recorded} == {"maker", "taker", None}
    assert ExecutionQualityEvidenceReason.LIQUIDITY_NOT_RECORDED in report.evidence.reasons


def test_unapplied_and_orphan_fills_are_excluded_and_counted() -> None:
    """Unapplied economics and orderless fills never enter the round-trip sums."""
    deployment = _deployment(uuid4())
    buy = _order(deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
    applied = _fill(buy, at=_at(0), price="100", fee="0.1")
    pending = _fill(buy, at=_at(1), price="101", fee="0.101", applied=False)
    orphan = Fill(
        id=uuid4(),
        deployment_id=deployment.id,
        order_id=uuid4(),
        venue_fill_id="orphan",
        price=Decimal("102"),
        quantity=Decimal("1"),
        fee=Decimal("0.102"),
        filled_at=_at(2),
        economics_applied_at=_at(2),
    )
    report = build_execution_quality_report(
        _snapshot(deployment, (buy,), (applied, pending, orphan)),
        journaled_closes=_closes(_at(0)),
    )
    book = report.books[0]
    assert book.open_cycle is not None
    assert len(book.open_cycle.entries) == 1
    assert report.evidence.unapplied_fill_count == 1
    assert report.evidence.orphan_fill_count == 1
    assert ExecutionQualityEvidenceReason.UNAPPLIED_FILL_ECONOMICS in report.evidence.reasons
    assert ExecutionQualityEvidenceReason.FILL_WITHOUT_ORDER in report.evidence.reasons


def test_over_covering_fill_is_clamped_and_flagged() -> None:
    """A sell larger than the held quantity closes the trip and discloses the flip."""
    deployment = _deployment(uuid4())
    buy = _order(deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100", quantity="1")
    big_sell = _order(
        deployment.id, OrderSide.SELL, OrderKind.MARKETABLE, price="110", quantity="2"
    )
    fills = (
        _fill(buy, at=_at(0), price="100", fee="0.1"),
        _fill(big_sell, at=_at(1), price="110", quantity="2", fee="0.22"),
    )
    report = build_execution_quality_report(
        _snapshot(deployment, (buy, big_sell), fills), journaled_closes=_closes(_at(0), _at(1))
    )
    trip = report.books[0].round_trips[0]
    assert trip.closed_quantity == "1"
    assert trip.exits[0].quantity == "1"
    assert trip.exit_fees == "0.22"  # the recorded fee stays whole on the clamped exit
    assert ExecutionQualityEvidenceReason.POSITION_FLIP_FILL in report.evidence.reasons


def test_ledger_realization_delta_is_disclosed_not_absorbed() -> None:
    """Fee-allocation rounding in the ledger surfaces as a delta reason, never as fees."""
    deployment = _deployment(uuid4())
    buy = _order(deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100", quantity="3")
    sells = tuple(
        _order(deployment.id, OrderSide.SELL, OrderKind.MARKETABLE, price="110", quantity="1")
        for _ in range(3)
    )
    fills = (
        _fill(buy, at=_at(0), price="100", quantity="3", fee="0.1"),
        *(
            _fill(sell, at=_at(index + 1), price="110", fee="0.11")
            for index, sell in enumerate(sells)
        ),
    )
    report = build_execution_quality_report(
        _snapshot(deployment, (buy, *sells), fills),
        journaled_closes=_closes(_at(0), _at(1), _at(2), _at(3)),
    )
    assert report.totals.net_pnl == "29.57"  # 30 - 0.1 - 0.33
    assert report.totals.ledger_realized_delta is not None
    if Decimal(report.totals.ledger_realized_delta) != 0:
        assert ExecutionQualityEvidenceReason.LEDGER_REALIZATION_DELTA in report.evidence.reasons


def test_multiple_products_fold_into_separate_books() -> None:
    """Each product's fills stay in their own book with their own closes."""
    deployment = _deployment(uuid4())
    btc_buy = _order(deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
    eth_buy = _order(
        deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="10", product_id="ETH-USD"
    )
    btc_sell = _order(deployment.id, OrderSide.SELL, OrderKind.MARKETABLE, price="110")
    eth_sell = _order(
        deployment.id, OrderSide.SELL, OrderKind.MARKETABLE, price="11", product_id="ETH-USD"
    )
    fills = (
        _fill(btc_buy, at=_at(0), price="100", fee="0.1"),
        _fill(eth_buy, at=_at(0, 30), price="10", fee="0.01"),
        _fill(btc_sell, at=_at(1), price="110", fee="0.11"),
        _fill(eth_sell, at=_at(1, 30), price="11", fee="0.011"),
    )
    closes = {
        ("BTC-USD", _at(-1)): JournaledDecisionClose(Decimal("101"), _at(0)),
        ("BTC-USD", _at(0)): JournaledDecisionClose(Decimal("109"), _at(1)),
        ("ETH-USD", _at(-1)): JournaledDecisionClose(Decimal("10"), _at(0)),
        ("ETH-USD", _at(0)): JournaledDecisionClose(Decimal("11"), _at(1)),
    }
    report = build_execution_quality_report(
        _snapshot(deployment, (btc_buy, eth_buy, btc_sell, eth_sell), fills),
        journaled_closes=closes,
    )
    assert [book.product_id for book in report.books] == ["BTC-USD", "ETH-USD"]
    assert report.books[0].net_pnl == "9.79"
    assert report.books[1].net_pnl == "0.979"
    assert report.totals.net_pnl == "10.769"


def test_unknown_timeframe_disables_close_matching_without_failing() -> None:
    """A book without a decision clock reports fills but no slippage evidence."""
    deployment = _deployment(uuid4(), timeframe=None)
    buy = _order(deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
    sell = _order(deployment.id, OrderSide.SELL, OrderKind.MARKETABLE, price="110")
    fills = (
        _fill(buy, at=_at(0), price="100", fee="0.1"),
        _fill(sell, at=_at(1), price="110", fee="0.11"),
    )
    report = build_execution_quality_report(_snapshot(deployment, (buy, sell), fills))
    assert report.books[0].round_trips[0].slippage_bps is None
    assert ExecutionQualityEvidenceReason.TIMEFRAME_UNKNOWN in report.evidence.reasons


def test_report_fingerprint_binds_content_and_stays_deterministic() -> None:
    """Identical evidence yields identical fingerprints; any change breaks one."""
    deployment = _deployment(uuid4())
    buy = _order(deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
    sell = _order(deployment.id, OrderSide.SELL, OrderKind.MARKETABLE, price="110")
    fills = (
        _fill(buy, at=_at(0), price="100", fee="0.1"),
        _fill(sell, at=_at(1), price="110", fee="0.11"),
    )
    snapshot = _snapshot(deployment, (buy, sell), fills)
    first = build_execution_quality_report(snapshot, journaled_closes=_closes(_at(0), _at(1)))
    second = build_execution_quality_report(snapshot, journaled_closes=_closes(_at(0), _at(1)))
    altered = build_execution_quality_report(snapshot)  # no closes: different evidence
    assert first.report_fingerprint == second.report_fingerprint
    assert first.report_fingerprint != altered.report_fingerprint


def _decision(
    deployment_id: UUID,
    bar: datetime,
    *,
    close: str | None,
    product_id: str = "BTC-USD",
) -> BarDecision:
    """One journaled bar decision carrying only the fields the report reads."""
    return BarDecision(
        deployment_id=deployment_id,
        product_id=product_id,
        timeframe="1h",
        mode=DeploymentMode.PAPER,
        bar_starts_at=bar,
        bar_closes_at=bar + HOUR,
        evaluated_at=bar + HOUR,
        outcome=DecisionOutcome.NO_SIGNAL,
        reason_code="NO_SIGNAL",
        summary="No entry signal on this bar.",
        close_price=close,
    )


@pytest.mark.anyio
async def test_journaled_close_evidence_pages_the_journal_for_needed_bars() -> None:
    """The loader reads the journal newest-first and returns the covered closes."""
    deployment = _deployment(uuid4())
    buy = _order(deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
    fill = _fill(buy, at=_at(3), price="100", fee="0.1")
    snapshot = _snapshot(deployment, (buy,), (fill,))
    store = InMemoryDecisionJournalStore()
    for hour in range(6):
        await store.upsert(_decision(deployment.id, _at(hour), close=str(100 + hour)))
    evidence = await load_journaled_close_evidence(
        store, deployment_id=deployment.id, snapshot=snapshot
    )
    assert evidence.coverage is not None
    assert evidence.coverage == (_at(0), _at(5))
    assert evidence.closes[("BTC-USD", _at(2))].price == Decimal("102")
    assert evidence.closes[("BTC-USD", _at(2))].bar_closes_at == _at(3)
    assert evidence.coverage_limited is False


@pytest.mark.anyio
async def test_twin_comparison_compares_complete_overlapping_evidence() -> None:
    """Two complete books with overlapping fills compare and normalize fees."""
    paper = _deployment(uuid4(), maker_rate="0.001", taker_rate="0.002")
    live = _deployment(uuid4(), mode=DeploymentMode.LIVE, maker_rate=None, taker_rate=None)
    reports = []
    snapshots = []
    for deployment in (paper, live):
        buy = _order(deployment.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
        sell = _order(deployment.id, OrderSide.SELL, OrderKind.MARKETABLE, price="110")
        fills = (
            _fill(buy, at=_at(0), price="100", fee="0.1"),
            _fill(sell, at=_at(1), price="110", fee="0.11"),
        )
        snapshot = _snapshot(deployment, (buy, sell), fills)
        snapshots.append(snapshot)
        reports.append(
            build_execution_quality_report(snapshot, journaled_closes=_closes(_at(0), _at(1)))
        )
    link = DeploymentTwinLink(
        paper_deployment_id=paper.id, live_deployment_id=live.id, linked_at=START
    )
    comparison = build_execution_twin_comparison(
        link=link,
        paper_snapshot=snapshots[0],
        paper_report=reports[0],
        live_snapshot=snapshots[1],
        live_report=reports[1],
    )
    assert comparison.comparable
    assert comparison.reasons == ()
    assert comparison.overlap is not None
    assert comparison.paper.net_pnl == comparison.live.net_pnl == "9.79"
    normalization = comparison.fee_normalization
    assert normalization is not None
    assert normalization.rate_source == "stored_paper_assumptions"
    assert normalization.observed_live_fees == "0.21"
    # maker entry 100*1*0.001 + taker exit 110*1*0.002
    assert normalization.counterfactual_live_fees_at_paper_rates == "0.32"
    assert normalization.fee_delta == "0.11"


@pytest.mark.anyio
async def test_twin_comparison_cannot_compare_without_overlap_or_complete_evidence() -> None:
    """Disjoint windows or incomplete evidence block the comparison with reasons."""
    paper = _deployment(uuid4(), maker_rate=None, taker_rate=None)
    live = _deployment(uuid4(), mode=DeploymentMode.LIVE, maker_rate=None, taker_rate=None)
    paper_buy = _order(paper.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
    paper_sell = _order(paper.id, OrderSide.SELL, OrderKind.MARKETABLE, price="110")
    paper_fills = (
        _fill(paper_buy, at=_at(0), price="100", fee="0.1"),
        _fill(paper_sell, at=_at(1), price="110", fee="0.11"),
    )
    live_buy = _order(live.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
    live_sell = _order(live.id, OrderSide.SELL, OrderKind.MARKETABLE, price="109")
    live_fills = (
        _fill(live_buy, at=_at(10), price="100", fee="0.1"),
        _fill(live_sell, at=_at(11), price="109", fee="0.109"),
    )
    paper_report = build_execution_quality_report(
        _snapshot(paper, (paper_buy, paper_sell), paper_fills),
        journaled_closes=_closes(_at(0), _at(1)),
    )
    # No journaled closes on the live side: incomplete evidence, and no overlap.
    live_report = build_execution_quality_report(_snapshot(live, (live_buy, live_sell), live_fills))
    link = DeploymentTwinLink(
        paper_deployment_id=paper.id, live_deployment_id=live.id, linked_at=START
    )
    comparison = build_execution_twin_comparison(
        link=link,
        paper_snapshot=_snapshot(paper, (paper_buy, paper_sell), paper_fills),
        paper_report=paper_report,
        live_snapshot=_snapshot(live, (live_buy, live_sell), live_fills),
        live_report=live_report,
    )
    assert comparison.comparable is False
    assert TwinComparisonReason.NO_OVERLAPPING_FILLS in comparison.reasons
    assert TwinComparisonReason.INCOMPLETE_LIVE_EVIDENCE in comparison.reasons
    assert comparison.fee_normalization is not None
    assert comparison.fee_normalization.rate_source == "documented_defaults"
    assert TwinComparisonReason.PAPER_FEE_RATES_DEFAULTED in comparison.reasons


@pytest.mark.anyio
async def test_twin_comparison_flags_entry_divergence_and_flipped_fills() -> None:
    """Entry-count divergence inside the overlap is disclosed, never paired by guess."""
    paper = _deployment(uuid4(), maker_rate="0.001", taker_rate="0.002")
    live = _deployment(uuid4(), mode=DeploymentMode.LIVE)
    paper_buy = _order(paper.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
    paper_sell = _order(paper.id, OrderSide.SELL, OrderKind.MARKETABLE, price="110")
    paper_fills = (
        _fill(paper_buy, at=_at(0), price="100", fee="0.1"),
        _fill(paper_sell, at=_at(1), price="110", fee="0.11"),
    )
    live_buy = _order(live.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
    live_add = _order(live.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="101")
    live_sell = _order(live.id, OrderSide.SELL, OrderKind.MARKETABLE, price="110", quantity="2")
    live_fills = (
        _fill(live_buy, at=_at(0), price="100", fee="0.1"),
        _fill(live_add, at=_at(0, 30), price="101", fee="0.101"),
        _fill(live_sell, at=_at(1), price="110", quantity="2", fee="0.22"),
    )
    closes = _closes(_at(0), _at(1))
    paper_report = build_execution_quality_report(
        _snapshot(paper, (paper_buy, paper_sell), paper_fills), journaled_closes=closes
    )
    live_report = build_execution_quality_report(
        _snapshot(live, (live_buy, live_add, live_sell), live_fills), journaled_closes=closes
    )
    link = DeploymentTwinLink(
        paper_deployment_id=paper.id, live_deployment_id=live.id, linked_at=START
    )
    comparison = build_execution_twin_comparison(
        link=link,
        paper_snapshot=_snapshot(paper, (paper_buy, paper_sell), paper_fills),
        paper_report=paper_report,
        live_snapshot=_snapshot(live, (live_buy, live_add, live_sell), live_fills),
        live_report=live_report,
    )
    assert TwinComparisonReason.ENTRY_FILL_COUNT_DIVERGENCE in comparison.reasons
    assert comparison.paper.entry_fill_count == 1
    assert comparison.live.entry_fill_count == 2
    assert comparison.fee_normalization is not None
    assert comparison.fee_normalization.fills_without_liquidity_evidence == 0


@pytest.mark.anyio
async def test_twin_comparison_rejects_mismatched_link_members() -> None:
    """A link whose members do not match the reports is refused, not reinterpreted."""
    paper = _deployment(uuid4())
    live = _deployment(uuid4(), mode=DeploymentMode.LIVE)
    other = _deployment(uuid4(), mode=DeploymentMode.LIVE)
    buy = _order(paper.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
    sell = _order(paper.id, OrderSide.SELL, OrderKind.MARKETABLE, price="110")
    fills = (
        _fill(buy, at=_at(0), price="100", fee="0.1"),
        _fill(sell, at=_at(1), price="110", fee="0.11"),
    )
    paper_report = build_execution_quality_report(
        _snapshot(paper, (buy, sell), fills), journaled_closes=_closes(_at(0), _at(1))
    )
    live_buy = _order(live.id, OrderSide.BUY, OrderKind.POST_ONLY_LIMIT, price="100")
    live_sell = _order(live.id, OrderSide.SELL, OrderKind.MARKETABLE, price="110")
    live_fills = (
        _fill(live_buy, at=_at(0), price="100", fee="0.1"),
        _fill(live_sell, at=_at(1), price="110", fee="0.11"),
    )
    live_report = build_execution_quality_report(
        _snapshot(live, (live_buy, live_sell), live_fills), journaled_closes=_closes(_at(0), _at(1))
    )
    link = DeploymentTwinLink(
        paper_deployment_id=paper.id, live_deployment_id=other.id, linked_at=START
    )
    try:
        build_execution_twin_comparison(
            link=link,
            paper_snapshot=_snapshot(paper, (buy, sell), fills),
            paper_report=paper_report,
            live_snapshot=_snapshot(live, (live_buy, live_sell), live_fills),
            live_report=live_report,
        )
    except ValueError as error:
        assert "do not match" in str(error)
    else:
        raise AssertionError("mismatched twin members must raise ValueError")
