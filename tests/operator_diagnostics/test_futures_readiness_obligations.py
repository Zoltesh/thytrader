"""Readiness retains live obligations across every account-mirror outcome."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, get_args

import pytest

from tests.alerts.test_futures_recovery_proof import live_policy, proven_book
from tests.operator_diagnostics.test_futures_readiness_reconciliation import _Store
from tests.operator_diagnostics.test_readiness_completeness import _Directory
from tests.operator_diagnostics.test_readiness_preflight import ScriptedExchange, _balance
from thytrader.exchanges.futures_models import (
    FuturesAccountObservation,
    FuturesBalanceSummary,
    FuturesEnablement,
)
from thytrader.execution.futures_visibility import visibility_scope
from thytrader.operator.readiness import build_readiness_report
from thytrader.operator.readiness_futures import futures_section
from thytrader.operator.readiness_models import ReadinessFinding, ReadinessSeverity
from thytrader.portfolio.service import PortfolioService
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentSnapshot, DeploymentStatus, OrderStatus

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.operator.readiness_models import ReadinessFuturesSection

pytestmark = pytest.mark.anyio

Obligation = Literal[
    "held",
    "stopped_held",
    "fresh_empty",
    "derisk",
    "flat_confirmation",
    "both_duties",
    "incomplete_stopped",
    "retired_history",
    "no_books",
]
Mirror = Literal[
    "absent",
    "empty",
    "read_error",
    "failed_status",
    "failed_positions",
    "stale",
    "future",
    "valid",
]


class _AccountingStore(InMemoryExecutionStore):
    """List real persisted deployments and expose typed accounting boundary fixtures."""

    def __init__(self, books: tuple[DeploymentSnapshot, ...]) -> None:
        """Keep immutable canonical snapshots, including an explicitly incomplete read."""
        super().__init__()
        self.books = {book.deployment.id: book for book in books}

    async def get_accounting_snapshot(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Return the selected complete or incomplete accounting read without projection."""
        return self.books[deployment_id]


async def _books(obligation: Obligation, now: datetime) -> tuple[DeploymentSnapshot, ...]:
    """Use a real filled ledger or an explicitly flat, stopped/no-history control."""
    if obligation == "no_books":
        return ()
    book, _, _ = await proven_book()
    if obligation in {"held", "stopped_held"}:
        if obligation == "stopped_held":
            book = replace(
                book, deployment=replace(book.deployment, status=DeploymentStatus.STOPPED)
            )
        assert visibility_scope(book) is True
        return (book,)
    retired = obligation == "retired_history"
    orders = (
        tuple(
            replace(order, status=OrderStatus.CANCELED, filled_quantity=Decimal(0))
            for order in book.orders
        )
        if retired
        else ()
    )
    book = replace(
        book,
        position=None,
        positions=(),
        fills=(),
        orders=orders,
        intents=book.intents if retired else (),
        accounting_complete=obligation != "incomplete_stopped",
        deployment=replace(
            book.deployment,
            status=DeploymentStatus.RUNNING
            if obligation == "fresh_empty"
            else DeploymentStatus.STOPPED,
            created_at=now if obligation == "fresh_empty" else book.deployment.created_at,
            futures_derisk_pending=obligation in {"derisk", "both_duties"},
            futures_flat_confirmation_after=now - timedelta(seconds=10)
            if obligation in {"flat_confirmation", "both_duties"}
            else None,
            mismatch_detail=None,
        ),
    )
    expected = None if obligation == "incomplete_stopped" else not retired
    assert visibility_scope(book) is expected
    if retired:
        assert book.accounting_complete and book.orders and book.intents
    return (book,)


def _mirror(state: Mirror, now: datetime) -> _Store | None:
    """Provide one receipt, no receipt, or a supported store failure; never venue I/O."""
    if state == "absent":
        return None
    if state in {"empty", "read_error"}:
        return _Store(None, fail=state == "read_error")
    balance = FuturesBalanceSummary(
        futures_buying_power=Decimal(100),
        total_usd_balance=Decimal(100),
        cbi_usd_balance=Decimal(0),
        cfm_usd_balance=Decimal(100),
        total_open_orders_hold_amount=Decimal(0),
        unrealized_pnl=Decimal(0),
        daily_realized_pnl=Decimal(0),
        initial_margin=Decimal(20),
        available_margin=Decimal(100),
        liquidation_threshold=Decimal(20),
        liquidation_buffer_amount=Decimal(80),
        liquidation_buffer_percentage=Decimal(80),
        total_pending_transfers_amount=Decimal(0),
        funding_pnl=Decimal(0),
        intraday_margin=None,
        overnight_margin=None,
    )
    observed_at = now - timedelta(seconds=1)
    if state == "stale":
        observed_at = now - timedelta(minutes=10)
    elif state == "future":
        observed_at = now + timedelta(minutes=10)
    return _Store(
        FuturesAccountObservation(
            observed_at=observed_at,
            enablement=FuturesEnablement.UNKNOWN
            if state == "failed_status"
            else FuturesEnablement.ENABLED,
            balance=None if state == "failed_status" else balance,
            positions=None if state == "failed_positions" else (),
            intraday_margin_setting=None,
            margin_window=None,
            read_failures=("balance_summary:transport",)
            if state == "failed_status"
            else ("positions:transport",)
            if state == "failed_positions"
            else (),
        )
    )


def _assert_evidence(
    section: ReadinessFuturesSection | None,
    findings: tuple[ReadinessFinding, ...],
    books: tuple[DeploymentSnapshot, ...],
    obligation: Obligation,
    state: Mirror,
) -> None:
    """Absence is unknown only when required; actual read failures remain account evidence."""
    required = obligation not in {"retired_history", "no_books"}
    missing = state in {"absent", "empty", "read_error"}
    codes = {finding.reason_code for finding in findings}
    unknown = [finding for finding in findings if finding.reason_code == "FUTURES_ACCOUNT_UNKNOWN"]
    expected_unknown = state not in {"absent", "empty", "valid"} or (required and missing)
    assert len(unknown) == int(expected_unknown)
    assert all(finding.severity is ReadinessSeverity.UNKNOWN for finding in unknown)
    assert ("FUTURES_LIVE_BOOKS" in codes) is required
    for active, code in (
        (obligation in {"derisk", "both_duties"}, "FUTURES_DERISK_PENDING"),
        (obligation in {"flat_confirmation", "both_duties"}, "FUTURES_FLAT_CONFIRMATION_PENDING"),
    ):
        duties = [finding for finding in findings if finding.reason_code == code]
        assert len(duties) == int(active)
        if active:
            assert duties[0].severity is ReadinessSeverity.VIOLATION
            assert duties[0].deployment_id == books[0].deployment.id
    if missing:
        assert section is None  # No fabricated timestamp, position count, balance or margin.
        assert "FUTURES_MARGIN_UNKNOWN" not in codes
        return
    assert section is not None
    assert len(section.live_books) == int(required)
    assert section.stale is (state in {"stale", "future"})
    assert section.positions == (None if state == "failed_positions" else ())
    assert section.available_margin == (None if state == "failed_status" else "100")
    assert ("POSITION_COMPARISON_UNPROVEN" in codes) is required
    if required:
        duty = section.live_books[0]
        assert duty.derisk_pending is books[0].deployment.futures_derisk_pending
        assert duty.flat_confirmation_after == books[0].deployment.futures_flat_confirmation_after


@pytest.mark.parametrize("obligation", get_args(Obligation))
@pytest.mark.parametrize("state", get_args(Mirror))
@pytest.mark.parametrize("public", [False, True], ids=["helper", "public_report"])
async def test_obligation_mirror_matrix(
    obligation: Obligation, state: Mirror, public: bool
) -> None:
    """Real readiness and its helper retain duties/uncertainty without inventing mirror facts."""
    now = datetime.now(UTC)
    books = await _books(obligation, now)
    store = _mirror(state, now)
    if public:
        execution = _AccountingStore(books)
        for book in books:
            await execution.create_deployment(book.deployment)
        report = await build_readiness_report(
            portfolio=PortfolioService(ScriptedExchange((_balance("USDC", "1000"),)), demo=False),
            execution=execution,
            risk_policies=None,
            portfolios=_Directory(()),
            futures_account=store,
        )
        section, findings = report.payload.futures, report.payload.findings
        if any(
            f.severity in {ReadinessSeverity.UNKNOWN, ReadinessSeverity.VIOLATION} for f in findings
        ):
            assert report.overall_status.value != "healthy"
        assert tuple(execution.books.values()) == books
    else:
        collected: list[ReadinessFinding] = []
        section = await futures_section(
            store, collected, snapshots=books, now=now, policy=live_policy()
        )
        findings = tuple(collected)
    _assert_evidence(section, findings, books, obligation, state)
    if section is not None:
        assert store is not None
        observation = await store.latest()
        assert observation is not None and section.observed_at == observation.observed_at
