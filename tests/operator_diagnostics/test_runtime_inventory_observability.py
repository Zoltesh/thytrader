"""Per-product occupied runtimes cannot borrow sibling inventory as flatness evidence."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from tests.alerts.test_evidence_recovery import _alert
from tests.execution.test_lifecycle_safety import _restart
from tests.operator_diagnostics.test_deployment_performance import _diagnostics
from tests.operator_diagnostics.test_projection_observability import _NOW, _no_candles, _order
from tests.operator_diagnostics.test_readiness_completeness import _Directory
from tests.operator_diagnostics.test_readiness_preflight import ScriptedExchange, _balance, _policy
from thytrader.alerts.models import AlertCheck, AlertCode
from thytrader.alerts.service import AlertService
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.alerts.supervision import AlertThresholds, gather_safety_findings
from thytrader.api.routes.deployment_serializers import snapshot_response
from thytrader.memory.notify import DisabledNotificationSender
from thytrader.operator.readiness import build_readiness_report
from thytrader.operator.venue_reconciliation import build_venue_reconciliation_report
from thytrader.portfolio.service import PortfolioService
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.trading.fill_ledger import unprojected_inventory_products, unsettled_fill_evidence
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    InstrumentRuntime,
    IntentPurpose,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
)
from thytrader.trading.protection import (
    PositionState,
    ProtectionStatus,
    book_protection_evidence,
    deployment_position_state,
    missing_occupied_inventory_products,
)

pytestmark = pytest.mark.anyio


async def _runtime_book(
    *, status: DeploymentStatus, phase: RuntimePhase, mode: DeploymentMode
) -> tuple[InMemoryExecutionStore, DeploymentSnapshot]:
    """Reload actual persisted runtime rows and BTC inventory; no owned-fill predicate holds."""
    store = InMemoryExecutionStore()
    deployment_id = uuid4()
    book = Deployment(
        id=deployment_id,
        strategy_id=uuid4(),
        strategy_fingerprint="sha256:" + "a" * 64,
        product_id="BTC-USD",
        mode=mode,
        status=status,
        phase=RuntimePhase.OPEN,
        cash=Decimal("10000"),
        initial_equity=Decimal("10000"),
        created_at=_NOW,
        updated_at=_NOW,
    )
    await store.create_deployment(book)
    await store.save_deployment(
        replace(
            book,
            status=status,
            phase=RuntimePhase.OPEN,
            timeframe="1h",
            allocated_capital=Decimal("10000"),
            performance_equity=Decimal("10500"),
            worker_lease_expires_at=_NOW + timedelta(seconds=30),
            mismatch_detail=None,
        )
    )
    await store.save_position(
        Position(
            deployment_id=deployment_id,
            product_id="BTC-USD",
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
            stop_price=Decimal("90"),
            target_price=Decimal("120"),
            entered_bar=_NOW,
            updated_at=_NOW,
        ),
        deployment_id=deployment_id,
        product_id="BTC-USD",
    )
    for product, runtime_phase in (("BTC-USD", RuntimePhase.OPEN), ("ETH-USD", phase)):
        await store.save_instrument_runtime(
            InstrumentRuntime(product_id=product, phase=runtime_phase), deployment_id=deployment_id
        )
    restarted = await _restart(await store.get_accounting_snapshot(deployment_id))
    return restarted, await restarted.get_accounting_snapshot(deployment_id)


@pytest.mark.parametrize("status", list(DeploymentStatus))
@pytest.mark.parametrize("phase", [RuntimePhase.OPEN, RuntimePhase.PENDING_EXIT])
@pytest.mark.parametrize("mode", list(DeploymentMode))
async def test_runtime_without_its_position_is_unknown_across_readers(
    status: DeploymentStatus, phase: RuntimePhase, mode: DeploymentMode
) -> None:
    """A surviving BTC row does not certify ETH flatness, cash-only equity or ownership."""
    store, snapshot = await _runtime_book(status=status, phase=phase, mode=mode)
    assert len(snapshot.positions) == 1 and len(snapshot.instrument_runtimes) == 2
    assert not unprojected_inventory_products(snapshot) and not unsettled_fill_evidence(snapshot)
    assert missing_occupied_inventory_products(snapshot) == ("ETH-USD",)
    eth = book_protection_evidence(snapshot, product_id="ETH-USD", position=None, now=_NOW)
    assert eth.status is ProtectionStatus.UNKNOWN and eth.required_quantity is None
    assert eth.reasons == ("runtime_position_unresolved",)
    btc = book_protection_evidence(
        snapshot, product_id="BTC-USD", position=snapshot.positions[0], now=_NOW
    )
    assert btc.required_quantity == Decimal("1")
    assert btc.status is (
        ProtectionStatus.COVERED if mode is DeploymentMode.PAPER else ProtectionStatus.UNPROTECTED
    )
    ledger = ledger_from_snapshot(snapshot, marks={"BTC-USD": Decimal("110")})
    assert not ledger.accounting_complete and not ledger.mark_complete
    assert ledger.equity is ledger.total_net_pnl is ledger.marked_exposure is None
    assert ledger.books[0].mark_complete  # Independent projected BTC evidence remains available.
    response = await snapshot_response(snapshot)
    assert response.ledger is not None and not response.ledger.mark_complete
    runtime = next(row for row in response.instrument_runtimes if row.product_id == "ETH-USD")
    assert runtime.phase == phase.value  # The reporting fix never mutates runtime state.
    assert response.position_state != "flat"
    report = await _diagnostics(execution=store).runtime_report(
        deployment_id=snapshot.deployment.id
    )
    eth_book = next(
        row for row in report.payload.deployments[0].books if row.product_id == "ETH-USD"
    )
    assert eth_book.position_state == "open_unverified"
    assert eth_book.protection.required_quantity is None
    assert response.capital.performance_equity is None
    assert deployment_position_state(snapshot) is not PositionState.FLAT
    policies = InMemoryRiskPolicyStore()
    await policies.publish(_policy(quote_currency="USD"))
    service = PortfolioService(
        ScriptedExchange((_balance("USD", "10000"), _balance("BTC", "1"), _balance("ETH", "2")))
    )
    readiness = await build_readiness_report(
        portfolio=service,
        execution=store,
        risk_policies=policies,
        portfolios=_Directory(()),
    )
    assert readiness.payload.inventory.status == "complete"
    assert readiness.payload.inventory.accounting_status == "unresolved"
    assert readiness.payload.deployments[0].exposure is None
    assert readiness.payload.deployments[0].allocation_remaining is None
    if mode is DeploymentMode.LIVE:
        account = readiness.payload.account
        assert account is not None and account.remaining_entry_capacity is None
        venue = await build_venue_reconciliation_report(portfolio=service, execution=store)
        assert venue.payload.managed_listing.status == "complete"
        assert venue.payload.managed_listing.accounting_status == "unresolved"
        assets = {row.currency: row for row in venue.payload.assets}
        assert assets["ETH"].classification == "managed_unknown"
        assert assets["ETH"].managed_net_quantity is assets["ETH"].foreign_quantity is None
        assert assets["BTC"].managed_net_quantity == "1"


@pytest.mark.parametrize("status", list(DeploymentStatus))
@pytest.mark.parametrize("phase", [RuntimePhase.OPEN, RuntimePhase.PENDING_EXIT, RuntimePhase.FLAT])
@pytest.mark.parametrize("mode", list(DeploymentMode))
async def test_missing_product_preserves_incidents_but_verified_flat_recovers(
    status: DeploymentStatus, phase: RuntimePhase, mode: DeploymentMode
) -> None:
    """Terminal ETH orders cannot clear occupied ETH; explicit flat rows recover without candles."""
    store, snapshot = await _runtime_book(status=status, phase=phase, mode=mode)
    book = snapshot.deployment
    stop = await _order(
        store,
        book,
        product="ETH-USD",
        purpose=IntentPurpose.STOP,
        side=OrderSide.SELL,
        status=OrderStatus.CANCELED,
        filled="0",
    )
    alerts = InMemoryAlertStore()
    codes = (
        AlertCode.STOP_UNCOVERED,
        AlertCode.STOP_COVERAGE_UNKNOWN,
        AlertCode.STOP_TRIGGERED_UNFILLED,
    )
    for code in codes:
        subject = (
            f"{book.id}:{stop.id}"
            if code is AlertCode.STOP_TRIGGERED_UNFILLED
            else f"{book.id}:ETH-USD"
        )
        await alerts.record(replace(_alert(book, code, subject), product_id="ETH-USD"), now=_NOW)
    for code, subject in (
        (AlertCode.STOP_COVERAGE_UNKNOWN, f"{book.id}:BTC-USD"),
        (AlertCode.BOOK_PAUSED_MISMATCH, str(book.id)),
    ):
        await alerts.record(_alert(book, code, subject), now=_NOW)
    evidence = await gather_safety_findings(
        deployments=(book,),
        snapshots=store,
        closed_candles=_no_candles,
        now=_NOW + timedelta(seconds=1),
        thresholds=AlertThresholds(),
        worker_interval_seconds=30,
        prior_alerts=await alerts.list_open_alerts(),
    )
    assert AlertCheck(AlertCode.STOP_COVERAGE_UNKNOWN, f"{book.id}:BTC-USD") in evidence.evaluated
    assert AlertCheck(AlertCode.BOOK_PAUSED_MISMATCH, str(book.id)) in evidence.evaluated
    occupied = phase is not RuntimePhase.FLAT
    for code in codes:
        subject = (
            f"{book.id}:{stop.id}"
            if code is AlertCode.STOP_TRIGGERED_UNFILLED
            else f"{book.id}:ETH-USD"
        )
        assert (AlertCheck(code, subject) not in evidence.evaluated) is occupied
    await AlertService(alerts, DisabledNotificationSender(), thresholds=AlertThresholds()).apply(
        evidence.findings,
        evaluated=evidence.evaluated,
        now=_NOW + timedelta(seconds=1),
    )
    remaining = await alerts.list_open_alerts()
    eth_remaining = tuple(row for row in remaining if row.product_id == "ETH-USD")
    assert {row.code for row in eth_remaining} == (set(codes) if occupied else set())
    # A known uncovered live BTC sibling still reports independently, not hidden by ETH.
    btc_remaining = tuple(row for row in remaining if row.product_id == "BTC-USD")
    assert {row.code for row in btc_remaining} == (
        {AlertCode.STOP_UNCOVERED} if mode is DeploymentMode.LIVE else set()
    )
    if not occupied:
        ledger = ledger_from_snapshot(
            await store.get_accounting_snapshot(book.id), marks={"BTC-USD": Decimal("110")}
        )
        assert ledger.accounting_complete and ledger.equity is not None


async def test_empty_legacy_occupied_phase_still_does_not_prove_flatness() -> None:
    """Removing every runtime and position does not weaken the original occupied/empty guard."""
    _, snapshot = await _runtime_book(
        status=DeploymentStatus.STOPPED, phase=RuntimePhase.FLAT, mode=DeploymentMode.PAPER
    )
    empty = replace(snapshot, position=None, positions=(), instrument_runtimes=())
    assert missing_occupied_inventory_products(empty) == ("BTC-USD",)
    assert deployment_position_state(empty) is PositionState.OPEN_UNVERIFIED
    assert not ledger_from_snapshot(empty).accounting_complete
