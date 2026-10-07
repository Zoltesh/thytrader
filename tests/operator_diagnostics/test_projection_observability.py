"""Cross-lane reporting and recovery from durable unresolved execution economics."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest

from tests.alerts.test_evidence_recovery import _alert
from tests.execution.test_lifecycle_safety import _restart
from tests.operator_diagnostics.test_deployment_performance import _diagnostics
from tests.operator_diagnostics.test_readiness_completeness import _aggregate, _Directory
from tests.operator_diagnostics.test_readiness_preflight import ScriptedExchange, _balance, _policy
from thytrader.alerts.models import AlertCheck, AlertCode, AlertSeverity
from thytrader.alerts.service import AlertService
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.alerts.supervision import AlertThresholds, gather_safety_findings
from thytrader.api.routes.deployment_serializers import snapshot_response, summary_response
from thytrader.exchanges.models import ExchangeOpenOrder
from thytrader.execution.fill_ledger import (
    ingest_fill,
    unprojected_inventory_products,
    unsettled_fill_evidence,
)
from thytrader.execution.ledger import ledger_from_snapshot
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    InstrumentRuntime,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
)
from thytrader.execution.overlay import InstrumentScopedStore
from thytrader.execution.protection import (
    PositionState,
    ProtectionStatus,
    book_position_state,
    book_protection_evidence,
    deployment_position_state,
    protection_evidence_response,
)
from thytrader.memory.notify import DisabledNotificationSender
from thytrader.operator.readiness import ReadinessReport, build_readiness_report
from thytrader.operator.venue_reconciliation import (
    VenueReconciliationReport,
    build_venue_reconciliation_report,
)
from thytrader.portfolio.service import PortfolioService
from thytrader.risk.opening_accounting import reconstruct_day_open
from thytrader.risk.store import InMemoryRiskPolicyStore

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.market_data.models import Candle

_NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
_Fault = Literal["unprojected", "canceled_partial", "filled_unpublished", "unapplied"]
pytestmark = pytest.mark.anyio


async def _order(
    store: InMemoryExecutionStore,
    book: Deployment,
    *,
    product: str,
    purpose: IntentPurpose = IntentPurpose.ENTRY,
    side: OrderSide = OrderSide.BUY,
    status: OrderStatus = OrderStatus.FILLED,
    filled: str = "1",
    price: str = "100",
    created_at: datetime = _NOW,
) -> Order:
    """Persist an intent-backed toy order, without submitting anything to a venue."""
    intent = OrderIntent(
        id=uuid4(),
        deployment_id=book.id,
        client_order_id=str(uuid4()),
        purpose=purpose,
        side=side,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        price=Decimal(price),
        created_at=created_at,
        candle_starts_at=created_at,
        product_id=product,
    )
    await store.save_intent(intent)
    order = Order(
        id=uuid4(),
        deployment_id=book.id,
        intent_id=intent.id,
        client_order_id=intent.client_order_id,
        venue_order_id=str(uuid4()),
        side=side,
        kind=intent.kind,
        quantity=intent.quantity,
        price=intent.price,
        status=status,
        filled_quantity=Decimal(filled),
        created_at=created_at,
        updated_at=created_at,
        product_id=product,
    )
    await store.save_order(order)
    return order


def _fill(order: Order, *, applied: bool) -> Fill:
    """One exact recorded execution; publication and economic application are separate."""
    return Fill(
        id=uuid4(),
        deployment_id=order.deployment_id,
        order_id=order.id,
        venue_fill_id=str(uuid4()),
        price=order.price or Decimal("100"),
        quantity=order.filled_quantity,
        fee=Decimal("0"),
        filled_at=order.created_at,
        economics_applied_at=order.created_at if applied else None,
    )


async def _fault_book(
    fault: _Fault,
    *,
    mode: DeploymentMode = DeploymentMode.LIVE,
    mismatch: str | None = None,
    portfolio_id: UUID | None = None,
) -> tuple[InMemoryExecutionStore, DeploymentSnapshot]:
    """Reload real rows after prior profit/opening proof and a later unresolved execution."""
    store = InMemoryExecutionStore()
    book = Deployment(
        id=uuid4(),
        strategy_id=uuid4(),
        strategy_fingerprint="sha256:" + "a" * 64,
        product_id="BTC-USD",
        mode=mode,
        status=DeploymentStatus.STOPPED,
        phase=RuntimePhase.FLAT,
        cash=Decimal("10500"),
        initial_equity=Decimal("10000"),
        allocated_capital=Decimal("1000000"),
        performance_capital_quote=Decimal("1000000"),
        performance_equity=Decimal("10500"),
        inventory_cost=Decimal("0"),
        reserved_buying_power=Decimal("0"),
        portfolio_id=portfolio_id,
        timeframe="1h",
        created_at=_NOW - timedelta(hours=6),
        updated_at=_NOW,
    )
    await store.create_deployment(book)
    # An actual earlier closed round trip proves 500 profit; it is not missing inventory.
    for side, price, purpose, offset in (
        (OrderSide.BUY, "100", IntentPurpose.ENTRY, 4),
        (OrderSide.SELL, "600", IntentPurpose.SIGNAL_EXIT, 3),
    ):
        prior = await _order(
            store,
            book,
            product="ETH-USD",
            side=side,
            price=price,
            purpose=purpose,
            created_at=_NOW - timedelta(hours=offset),
        )
        await store.save_fill(_fill(prior, applied=True))
    before = await store.get_accounting_snapshot(book.id)
    evidence = reconstruct_day_open(before, as_of=_NOW)
    assert evidence is not None and evidence.equity == Decimal("10000")
    await store.save_deployment(replace(book, risk_day_open_evidence=evidence))
    order = await _order(
        store,
        book,
        product="BTC-USD",
        status=OrderStatus.FILLED
        if fault in {"unprojected", "filled_unpublished"}
        else OrderStatus.CANCELED,
        filled="1" if fault in {"unprojected", "filled_unpublished"} else "0.4",
    )
    if fault == "unprojected":
        # Missing durable stop geometry really applies cash but cannot project a position.
        await ingest_fill(
            await store.get_accounting_snapshot(book.id),
            fill=_fill(order, applied=False),
            order=order,
            store=store,
        )
    elif fault == "unapplied":
        await store.save_fill(_fill(order, applied=False))
    latest = await store.get_accounting_snapshot(book.id)
    await store.save_deployment(
        replace(latest.deployment, mismatch_detail=mismatch, phase=RuntimePhase.FLAT)
    )
    await store.save_instrument_runtime(
        InstrumentRuntime(product_id="BTC-USD", phase=RuntimePhase.FLAT), deployment_id=book.id
    )
    reloaded = await _restart(await store.get_accounting_snapshot(book.id))
    return reloaded, await reloaded.get_accounting_snapshot(book.id)


@pytest.mark.parametrize(
    "fault", ["unprojected", "canceled_partial", "filled_unpublished", "unapplied"]
)
@pytest.mark.parametrize("mismatch", [None, "Unrelated later display mismatch."])
@pytest.mark.parametrize("mode", [DeploymentMode.LIVE, DeploymentMode.PAPER])
async def test_durable_unresolved_books_never_report_flat_or_complete(
    fault: _Fault, mismatch: str | None, mode: DeploymentMode
) -> None:
    """Clearing/overwriting display faults and prior opening/profits cannot repair projection."""
    store, snapshot = await _fault_book(fault, mode=mode, mismatch=mismatch)
    assert snapshot.position is None and not snapshot.positions
    assert snapshot.deployment.risk_day_open_evidence is not None
    if fault == "unprojected":
        assert unprojected_inventory_products(snapshot) == ("BTC-USD",)
        assert not unsettled_fill_evidence(snapshot)
    else:
        assert unsettled_fill_evidence(snapshot)
    protection = book_protection_evidence(snapshot, product_id="BTC-USD", position=None, now=_NOW)
    assert protection.status is ProtectionStatus.UNKNOWN
    assert protection.required_quantity is protection.covered_quantity is None
    assert protection.uncovered_quantity is None
    assert not protection.venue_resting and protection.verified_at is None
    assert protection_evidence_response(protection).required_quantity is None
    assert (
        book_position_state(
            snapshot,
            product_id="BTC-USD",
            position=None,
            phase=RuntimePhase.FLAT,
            evidence=protection,
        )
        is PositionState.OPEN_UNVERIFIED
    )
    assert deployment_position_state(snapshot) is PositionState.OPEN_UNVERIFIED
    ledger = ledger_from_snapshot(snapshot, marks={"BTC-USD": Decimal("200")})
    assert not ledger.mark_complete and not ledger.accounting_complete
    assert ledger.equity is ledger.total_net_pnl is ledger.unrealized_net_pnl is None
    assert ledger.marked_exposure is ledger.base_quantity is None
    assert ledger.maximum_drawdown is ledger.maximum_drawdown_fraction is None
    assert ledger.realized_net_pnl == Decimal("500")  # exact known recorded population
    response = await snapshot_response(snapshot)
    assert response.position_state == "open_unverified" and not response.positions
    assert response.ledger is not None and not response.ledger.mark_complete
    assert response.ledger.total_net_pnl is None
    assert response.capital.performance_equity is response.capital.inventory_cost is None
    assert response.capital.reserved_buying_power is None
    assert response.capital.initial_equity == "10000"
    assert response.capital.performance_capital_quote == "1000000"
    summary = await summary_response(await store.get_deployment_summary(snapshot.deployment.id))
    assert summary.position_state == "open_unverified"
    assert summary.ledger is not None and summary.ledger.total_net_pnl is None
    diagnostics = _diagnostics(execution=store)
    runtime = await diagnostics.runtime_report(deployment_id=snapshot.deployment.id)
    assert runtime.payload.deployments[0].position_state == "open_unverified"
    assert runtime.payload.deployments[0].books[0].protection.required_quantity is None
    performance = await diagnostics.performance(deployment_id=snapshot.deployment.id)
    assert performance.payload.total_net_pnl is None and not performance.payload.mark_complete
    assert performance.components[0].reason_code == "ACCOUNTING_UNRESOLVED"


@pytest.mark.parametrize(
    "fault", ["unprojected", "canceled_partial", "filled_unpublished", "unapplied"]
)
async def test_readiness_and_venue_separate_read_success_from_economic_completeness(
    fault: _Fault,
) -> None:
    """All full reads can succeed while capacity and BTC ownership remain unknown."""
    aggregate = _aggregate(quote="USD")
    store, snapshot = await _fault_book(fault, portfolio_id=aggregate.portfolio.portfolio_id)
    service = PortfolioService(
        ScriptedExchange(
            (_balance("USD", "1000000"), _balance("BTC", "1"), _balance("ETH", "2")),
            orders=(ExchangeOpenOrder("independent-foreign-order", "ETH-USD", "buy", "OPEN"),),
        )
    )
    policies = InMemoryRiskPolicyStore()
    await policies.publish(_policy(quote_currency="USD"))
    report = await build_readiness_report(
        portfolio=service,
        execution=InstrumentScopedStore(store, "ETH-USD"),
        risk_policies=policies,
        portfolios=_Directory((aggregate,)),
        deployment_id=snapshot.deployment.id,
    )
    # The real scoped adapter must forward a fresh full accounting read, not hide BTC fills.
    evidence = report.payload.inventory
    assert evidence.status == "complete" and evidence.read_books == evidence.expected_books == 1
    assert evidence.accounting_status == "unresolved"
    assert evidence.unresolved_deployment_ids == (snapshot.deployment.id,)
    account = report.payload.account
    assert account is not None and account.venue_available_quote == "1000000"
    assert (
        account.capital_base is account.current_exposure is account.remaining_entry_capacity is None
    )
    row = report.payload.deployments[0]
    assert row.inventory_cost is row.exposure is row.allocation_remaining is None
    assert row.allocated_capital == "1000000"  # independent stored sizing limit remains known
    assert row.working_entry_reserved == "0" and account.working_buy_entry_reserved == "0"
    section = report.payload.portfolios[0]
    assert section.inventory.status == "complete"
    assert section.inventory.accounting_status == "unresolved"
    assert section.current_total_exposure is section.remaining_total_capacity is None
    assert report.overall_status.value == "degraded"
    venue = await build_venue_reconciliation_report(
        portfolio=service, execution=InstrumentScopedStore(store, "ETH-USD")
    )
    assert venue.payload.managed_listing.status == "complete"
    assert venue.payload.managed_listing.accounting_status == "unresolved"
    assert venue.payload.managed_listing.unresolved_deployment_ids == (snapshot.deployment.id,)
    assert (
        venue.payload.balances_listing.status == venue.payload.orders_listing.status == "complete"
    )
    btc = next(row for row in venue.payload.assets if row.currency == "BTC")
    assert btc.classification == "managed_unknown" and btc.venue_quantity == "1"
    assert btc.managed_net_quantity is btc.foreign_quantity is None
    eth = next(row for row in venue.payload.assets if row.currency == "ETH")
    assert eth.classification == "external_inventory" and eth.foreign_quantity == "2"
    assert venue.payload.orders.foreign is not None and len(venue.payload.orders.foreign) == 1
    assert venue.overall_status.value == "degraded"
    assert ReadinessReport.model_validate_json(report.model_dump_json()) == report
    assert VenueReconciliationReport.model_validate_json(venue.model_dump_json()) == venue


async def _no_candles(
    product_id: str, timeframe: str, deploy_anchor: datetime
) -> tuple[Candle, ...]:
    """Reporting needs no invented price/bar evidence to identify durable incompleteness."""
    del product_id, timeframe, deploy_anchor
    return ()


@pytest.mark.parametrize(
    "fault", ["unprojected", "canceled_partial", "filled_unpublished", "unapplied"]
)
@pytest.mark.parametrize("phase", [RuntimePhase.FLAT, RuntimePhase.OPEN])
async def test_unresolved_economics_do_not_recover_prior_protection_or_terminal_trigger(
    fault: _Fault,
    phase: RuntimePhase,
) -> None:
    """Restarted alert service holds unknown inventory incidents, not independent row checks."""
    store, snapshot = await _fault_book(fault)
    book = replace(
        snapshot.deployment, phase=phase, worker_lease_expires_at=_NOW + timedelta(seconds=30)
    )
    await store.save_deployment(book)
    stop = await _order(
        store,
        book,
        product="BTC-USD",
        purpose=IntentPurpose.STOP,
        side=OrderSide.SELL,
        status=OrderStatus.CANCELED,
        filled="0",
    )
    independent = await _order(
        store,
        book,
        product="ETH-USD",
        purpose=IntentPurpose.STOP,
        side=OrderSide.SELL,
        status=OrderStatus.CANCELED,
        filled="0",
    )
    alerts = InMemoryAlertStore()
    for code, subject in (
        (AlertCode.STOP_UNCOVERED, f"{book.id}:BTC-USD"),
        (AlertCode.STOP_COVERAGE_UNKNOWN, f"{book.id}:BTC-USD"),
        (AlertCode.STOP_TRIGGERED_UNFILLED, f"{book.id}:{stop.id}"),
        (AlertCode.BOOK_PAUSED_MISMATCH, str(book.id)),
    ):
        await alerts.record(_alert(book, code, subject), now=_NOW)
    await alerts.record(
        replace(
            _alert(book, AlertCode.STOP_TRIGGERED_UNFILLED, f"{book.id}:{independent.id}"),
            product_id="ETH-USD",
        ),
        now=_NOW,
    )
    evidence = await gather_safety_findings(
        deployments=(book,),
        snapshots=store,
        closed_candles=_no_candles,
        now=_NOW + timedelta(seconds=1),
        thresholds=AlertThresholds(),
        worker_interval_seconds=30,
        prior_alerts=await alerts.list_open_alerts(),
    )
    for code in (AlertCode.STOP_UNCOVERED, AlertCode.STOP_COVERAGE_UNKNOWN):
        assert AlertCheck(code, f"{book.id}:BTC-USD") not in evidence.evaluated
    assert (
        AlertCheck(AlertCode.STOP_TRIGGERED_UNFILLED, f"{book.id}:{stop.id}")
        not in evidence.evaluated
    )
    assert (
        AlertCheck(AlertCode.STOP_TRIGGERED_UNFILLED, f"{book.id}:{independent.id}")
        in evidence.evaluated
    )
    await AlertService(alerts, DisabledNotificationSender(), thresholds=AlertThresholds()).apply(
        evidence.findings, evaluated=evidence.evaluated, now=_NOW + timedelta(seconds=1)
    )
    remaining = await alerts.list_open_alerts()
    assert {row.code for row in remaining} == {
        AlertCode.STOP_UNCOVERED,
        AlertCode.STOP_COVERAGE_UNKNOWN,
        AlertCode.STOP_TRIGGERED_UNFILLED,
    }
    assert all(row.product_id == "BTC-USD" for row in remaining)


async def test_complete_applied_exit_economics_can_prove_true_flatness_and_recovery() -> None:
    """Conservative unknown is not permanent when retained applied executions actually net flat."""
    store, snapshot = await _fault_book("unprojected")
    book = snapshot.deployment
    exit_order = await _order(
        store,
        book,
        product="BTC-USD",
        purpose=IntentPurpose.SIGNAL_EXIT,
        side=OrderSide.SELL,
        created_at=_NOW + timedelta(seconds=1),
    )
    await store.save_fill(_fill(exit_order, applied=True))
    await store.save_deployment(replace(book, cash=Decimal("10500")))
    flat = await store.get_accounting_snapshot(book.id)
    assert not unprojected_inventory_products(flat) and not unsettled_fill_evidence(flat)
    assert deployment_position_state(flat) is PositionState.FLAT
    protection = book_protection_evidence(flat, product_id="BTC-USD", position=None, now=_NOW)
    assert protection.status is ProtectionStatus.FLAT and protection.required_quantity == Decimal(0)
    ledger = ledger_from_snapshot(flat)
    assert ledger.accounting_complete and ledger.mark_complete
    assert ledger.total_net_pnl == Decimal("500")
    alerts = InMemoryAlertStore()
    for code, subject in (
        (AlertCode.STOP_UNCOVERED, f"{book.id}:BTC-USD"),
        (AlertCode.STOP_TRIGGERED_UNFILLED, f"{book.id}:{exit_order.id}"),
    ):
        await alerts.record(_alert(book, code, subject), now=_NOW)
    observed = await gather_safety_findings(
        deployments=(flat.deployment,),
        snapshots=store,
        closed_candles=_no_candles,
        now=_NOW + timedelta(seconds=2),
        thresholds=AlertThresholds(),
        worker_interval_seconds=30,
        prior_alerts=await alerts.list_open_alerts(),
    )
    await AlertService(alerts, DisabledNotificationSender(), thresholds=AlertThresholds()).apply(
        observed.findings, evaluated=observed.evaluated, now=_NOW + timedelta(seconds=2)
    )
    assert not await alerts.list_open_alerts()


async def test_unresolved_other_quote_does_not_poison_independent_account_quote() -> None:
    """Actual products scope accounting: USD and USDC are not added or converted."""
    store, snapshot = await _fault_book("unprojected")
    for order in snapshot.orders:
        if order.product_id == "BTC-USD":
            await store.save_order(replace(order, product_id="BTC-USDC"))
    policies = InMemoryRiskPolicyStore()
    await policies.publish(_policy(quote_currency="USD"))
    report = await build_readiness_report(
        portfolio=PortfolioService(ScriptedExchange((_balance("USD", "1000"),))),
        execution=store,
        risk_policies=policies,
        portfolios=_Directory(()),
    )
    assert report.payload.inventory.accounting_status == "unresolved"
    account = report.payload.account
    assert account is not None and account.inventory.accounting_status == "complete"
    assert account.capital_base == "1000" and account.current_exposure == "0"
    quotes = report.payload.deployments[0].quote_exposures
    assert {row.quote_currency: row.exposure for row in quotes} == {"USD": "0", "USDC": None}


async def test_unresolved_product_does_not_block_other_products_positive_fault() -> None:
    """A known uncovered ETH book still alerts while BTC economics remain unresolved."""
    store, snapshot = await _fault_book("unprojected")
    book = replace(snapshot.deployment, worker_lease_expires_at=_NOW + timedelta(seconds=30))
    await store.save_deployment(book)
    position = Position(
        deployment_id=book.id,
        product_id="ETH-USD",
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        target_price=Decimal("120"),
        entered_bar=_NOW,
        updated_at=_NOW,
    )
    await store.save_position(position, deployment_id=book.id, product_id="ETH-USD")
    evidence = await gather_safety_findings(
        deployments=(book,),
        snapshots=store,
        closed_candles=_no_candles,
        now=_NOW,
        thresholds=AlertThresholds(),
        worker_interval_seconds=30,
    )
    assert {(row.code, row.product_id) for row in evidence.findings} == {
        (AlertCode.STOP_UNCOVERED, "ETH-USD"),
        (AlertCode.STOP_COVERAGE_UNKNOWN, "BTC-USD"),
    }


@pytest.mark.parametrize(
    ("stop_status", "expected"),
    [
        (OrderStatus.CANCELED, (AlertCode.STOP_UNCOVERED, AlertSeverity.CRITICAL)),
        (OrderStatus.PENDING, (AlertCode.STOP_COVERAGE_UNKNOWN, AlertSeverity.WARNING)),
        (OrderStatus.UNKNOWN, (AlertCode.STOP_COVERAGE_UNKNOWN, AlertSeverity.WARNING)),
    ],
)
async def test_unresolved_live_book_without_any_working_stop_stays_critical(
    stop_status: OrderStatus, expected: tuple[AlertCode, AlertSeverity]
) -> None:
    """Unknown quantity softens a working stop to unknown cover, never a missing stop."""
    store, snapshot = await _fault_book("unapplied")
    book = replace(snapshot.deployment, worker_lease_expires_at=_NOW + timedelta(seconds=30))
    await store.save_deployment(book)
    position = Position(
        deployment_id=book.id,
        product_id="BTC-USD",
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        target_price=Decimal("120"),
        entered_bar=_NOW,
        updated_at=_NOW,
    )
    await store.save_position(position, deployment_id=book.id, product_id="BTC-USD")
    stop = await _order(
        store,
        book,
        product="BTC-USD",
        purpose=IntentPurpose.STOP,
        side=OrderSide.SELL,
        status=stop_status,
        filled="0",
        price="89",
    )
    await store.save_order(
        replace(stop, kind=OrderKind.STOP_LIMIT, stop_trigger_price=Decimal("90"))
    )
    evidence = await gather_safety_findings(
        deployments=(book,),
        snapshots=store,
        closed_candles=_no_candles,
        now=_NOW,
        thresholds=AlertThresholds(),
        worker_interval_seconds=30,
    )
    btc = {(row.code, row.severity) for row in evidence.findings if row.product_id == "BTC-USD"}
    assert expected in btc
    assert ((AlertCode.STOP_UNCOVERED, AlertSeverity.CRITICAL) in btc) is (
        expected[0] is AlertCode.STOP_UNCOVERED
    )


async def test_valid_focused_product_evidence_is_not_complete_shared_accounting() -> None:
    """An independent projected paper book keeps its local stop evidence, not aggregate PnL."""
    store, snapshot = await _fault_book("unprojected", mode=DeploymentMode.PAPER)
    position = Position(
        deployment_id=snapshot.deployment.id,
        product_id="ETH-USD",
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        target_price=Decimal("120"),
        entered_bar=_NOW,
        updated_at=_NOW,
    )
    await store.save_position(position, deployment_id=snapshot.deployment.id, product_id="ETH-USD")
    focused = await InstrumentScopedStore(store, "ETH-USD").get_deployment(snapshot.deployment.id)
    evidence = book_protection_evidence(focused, product_id="ETH-USD", position=position, now=_NOW)
    assert evidence.status is ProtectionStatus.COVERED and evidence.required_quantity == Decimal(
        "1"
    )
    ledger = ledger_from_snapshot(focused, marks={"ETH-USD": Decimal("110")})
    assert not ledger.accounting_complete and ledger.equity is None and not ledger.mark_complete
    assert ledger.books and ledger.books[0].mark_complete
