"""Operator ``futures-books`` projection of paper futures books (ADR 0129 §4, P1-6).

The figures are hand-computed: equity is cash plus signed base quantity at the mark,
initial = maintenance = notional x the overnight rate of the held side, and the
liquidation price solves equity = maintenance. Unknown evidence stays ``null`` and is
named in ``unknown`` and ``entry_blocks``; it is never zero.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.execution.test_paper_futures_books import (
    _CONTRACT,
    _PERP,
    _filled_book,
    _observation,
    _policy,
)
from thytrader.market_data.futures_observations import (
    FundingRateRecord,
    FuturesInstrumentObservation,
)
from thytrader.operator.futures_books_report import (
    FuturesBookPayload,
    book_payload,
    build_futures_books_report,
    effective_policy_or_none,
    futures_book_payload,
)
from thytrader.operator.models import ReportStatus
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.models import (
    ActiveRiskPolicy,
    RiskPolicyDefinition,
    compiled_default_risk_policy,
)
from thytrader.risk.store import InMemoryRiskPolicyStore, RiskPolicyStoreError
from thytrader.trading.futures_book import (
    BoundFuturesContract,
    InMemoryFuturesContractStore,
    funding_hours_held,
)
from thytrader.trading.ids import uuid7
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionStoreError,
    Position,
    PositionSide,
    RuntimePhase,
)

if TYPE_CHECKING:
    from uuid import UUID

_AT = datetime(2026, 10, 10, 12, tzinfo=UTC)
_RATES = {"overnight_long_margin_rate": "0.2", "overnight_short_margin_rate": "0.25"}


def _deployment(
    *, cash: str, product_id: str = _PERP, status: DeploymentStatus | None = None
) -> Deployment:
    """A paper book that started with 50 USD."""
    return Deployment(
        id=uuid7(_AT),
        strategy_fingerprint="sha256:" + "a" * 64,
        strategy_id=None,
        strategy_name="Perp hedge",
        product_id=product_id,
        timeframe="1h",
        mode=DeploymentMode.PAPER,
        status=status or DeploymentStatus.RUNNING,
        paper_starting_cash=Decimal(50),
        paper_maker_fee_rate=Decimal(0),
        paper_taker_fee_rate=Decimal("0.0005"),
        cash=Decimal(cash),
        phase=RuntimePhase.OPEN,
        created_at=_AT,
        updated_at=_AT,
    )


def _snapshot(*, side: PositionSide, cash: str) -> DeploymentSnapshot:
    """One BTC (100 contracts of 0.01) held from 100 USD."""
    deployment = _deployment(cash=cash)
    position = Position(
        deployment_id=deployment.id,
        quantity=Decimal(1),
        entry_price=Decimal(100),
        stop_price=Decimal(80) if side is PositionSide.LONG else Decimal(120),
        target_price=None,
        entered_bar=_AT - timedelta(hours=2),
        updated_at=_AT,
        side=side,
        product_id=_PERP,
    )
    return DeploymentSnapshot(deployment=deployment, orders=(), fills=(), position=position)


def _binding(deployment_id: UUID) -> BoundFuturesContract:
    """The BIP perp bound with a 0.15 USD per-contract fee."""
    return BoundFuturesContract(
        deployment_id=deployment_id,
        contract=_CONTRACT,
        fee_per_contract=Decimal("0.15"),
        bound_at=_AT,
    )


_UNSET = object()


def _project(
    snapshot: DeploymentSnapshot,
    mark: str | None,
    *,
    bound: bool = True,
    observation: FuturesInstrumentObservation | object | None = _UNSET,
    policy: RiskPolicyDefinition | object | None = _UNSET,
    now: datetime = _AT,
) -> FuturesBookPayload:
    """Project with the 0.2 / 0.25 overnight rates and the futures policy by default."""
    chosen = replace(_observation(), **_RATES) if observation is _UNSET else observation
    effective = _policy() if policy is _UNSET else policy
    assert chosen is None or isinstance(chosen, FuturesInstrumentObservation)
    assert effective is None or isinstance(effective, RiskPolicyDefinition)
    return book_payload(
        snapshot,
        binding=_binding(snapshot.deployment.id) if bound else None,
        observation=chosen,
        observed_at=None if chosen is None else _AT,
        mark=None if mark is None else (Decimal(mark), _AT),
        policy=effective,
        now=now,
    )


def test_a_levered_long_shows_margin_buffer_and_liquidation_price() -> None:
    """Long 1 BTC from 100 on 50 USD (cash -50) marked at 110."""
    book = _project(_snapshot(side=PositionSide.LONG, cash="-50"), "110")
    assert (book.side, book.contracts, book.base_quantity) == ("long", "100", "1")
    assert book.equity == "60"
    assert book.notional == "110"
    assert book.leverage == "1.833333"
    assert book.initial_margin == book.maintenance_margin == "22"
    assert book.liquidation_buffer_fraction == "0.633333"
    assert book.min_liquidation_buffer_fraction == "0.5"
    # -cash / (q (1 - r)) = 50 / 0.8; at 62.5 equity 12.5 equals maintenance 12.5.
    assert book.liquidation_price == "62.5"
    assert book.currency == "USD"
    assert book.fee_per_contract == "0.15"
    assert (book.maker_fee_rate, book.taker_fee_rate) == ("0", "0.0005")
    assert book.entry_blocks == ()
    assert book.unknown == ()


def test_a_short_uses_the_short_rate_and_a_rising_liquidation_price() -> None:
    """Short 1 BTC from 100 on 50 USD (cash 150) marked at 90."""
    book = _project(_snapshot(side=PositionSide.SHORT, cash="150"), "90")
    assert book.side == "short"
    assert book.equity == "60"
    assert book.notional == "90"
    assert book.leverage == "1.5"
    assert book.initial_margin == "22.5"
    assert book.liquidation_buffer_fraction == "0.625"
    # cash / (q (1 + r)) = 150 / 1.25; at 120 equity 30 equals maintenance 30.
    assert book.liquidation_price == "120"


def test_a_cash_covered_long_has_no_liquidation_price() -> None:
    """A long whose cash covers the notional cannot reach maintenance above zero."""
    book = _project(_snapshot(side=PositionSide.LONG, cash="10"), "110")
    assert book.liquidation_price is None
    assert book.equity == "120"


def test_unknown_evidence_stays_null_and_denies_entries() -> None:
    """No binding, no mark and no rates: dependent figures are null, never zero."""
    snapshot = _snapshot(side=PositionSide.LONG, cash="-50")
    book = _project(snapshot, None, bound=False, observation=None)
    assert book.contracts is None
    assert book.contract_kind is None
    assert book.equity is None
    assert book.notional is None
    assert book.leverage is None
    assert book.initial_margin is None
    assert book.liquidation_buffer_fraction is None
    assert book.liquidation_price is None
    assert book.unknown == ("binding", "mark", "margin_rates")
    assert book.entry_blocks == ("FUTURES_CONTRACT_UNBOUND", "FUTURES_MARGIN_UNKNOWN")


def test_out_of_range_rates_are_unknown_like_the_worker() -> None:
    """A rate above 1 is not a margin rate; the worker would deny, so the view does too."""
    snapshot = _snapshot(side=PositionSide.LONG, cash="-50")
    observation = replace(_observation(), overnight_long_margin_rate="1.5")
    book = _project(snapshot, "110", observation=observation)
    assert "margin_rates" in book.unknown
    assert book.initial_margin is None


def test_an_unset_futures_policy_and_an_unreadable_policy_are_distinguished() -> None:
    """Unset denies every futures entry; unreadable leaves the minimum buffer unknown."""
    snapshot = _snapshot(side=PositionSide.LONG, cash="-50")
    unset = _project(snapshot, "110", policy=compiled_default_risk_policy())
    assert unset.entry_blocks == ("FUTURES_POLICY_UNSET",)
    assert unset.min_liquidation_buffer_fraction == "0.5"
    assert unset.policy_max_leverage is None
    unreadable = _project(snapshot, "110", policy=None)
    assert unreadable.unknown == ("policy",)
    assert unreadable.min_liquidation_buffer_fraction is None
    capped = compiled_default_risk_policy().model_copy(
        update={
            "futures": FuturesRiskPolicy(
                paper_capital_usd="1000",
                max_leverage="3",
                min_liquidation_buffer_fraction="0.4",
            )
        }
    )
    tight = _project(snapshot, "110", policy=capped)
    assert (tight.policy_max_leverage, tight.min_liquidation_buffer_fraction) == ("3", "0.4")


@pytest.mark.anyio
async def test_an_unapplied_held_funding_hour_turns_overdue_after_the_grace() -> None:
    """The first held hour without a funding entry is overdue 75 minutes after it."""
    store = InMemoryExecutionStore()
    snapshot = await _filled_book(store)
    hours = funding_hours_held(
        snapshot, _PERP, through=snapshot.deployment.updated_at + timedelta(days=1)
    )
    first = hours[0]
    fresh = _project(snapshot, "130", now=first + timedelta(minutes=74))
    assert fresh.funding_overdue_since is None
    assert "FUNDING_HISTORY_MISSING" not in fresh.entry_blocks
    overdue = _project(snapshot, "130", now=first + timedelta(minutes=75))
    assert overdue.funding_overdue_since == first
    assert "funding" in overdue.unknown
    assert "FUNDING_HISTORY_MISSING" in overdue.entry_blocks


class _FailingExecution(InMemoryExecutionStore):
    """Execution storage that cannot list deployments."""

    async def list_deployments(
        self, *, limit: int | None = None, offset: int = 0
    ) -> tuple[Deployment, ...]:
        """Fail like an unreachable database."""
        del limit, offset
        raise ExecutionStoreError("down")


class _Latest:
    """The BIP observation with known rates."""

    async def latest_instrument(
        self, product_id: str
    ) -> tuple[FuturesInstrumentObservation, datetime] | None:
        """Return the rated observation for the perp only."""
        if product_id != _PERP:
            return None
        return replace(_observation(), **_RATES), _AT

    async def funding_rates(
        self, *, product_id: str | None, starts_at: datetime, ends_at: datetime
    ) -> tuple[FundingRateRecord, ...]:
        """The report never reads funding rates."""
        del product_id, starts_at, ends_at
        raise AssertionError("not read")


@pytest.mark.anyio
async def test_the_report_lists_futures_books_only_and_sums_the_usd_envelope() -> None:
    """Spot books are excluded; stopped books are listed but draw nothing from the envelope."""
    store = InMemoryExecutionStore()
    futures = _deployment(cash="50")
    await store.create_deployment(futures)
    stopped = replace(_deployment(cash="50", status=DeploymentStatus.STOPPED), id=uuid7(_AT))
    await store.create_deployment(stopped)
    spot = replace(_deployment(cash="50", product_id="BTC-USDC"), id=uuid7(_AT))
    await store.create_deployment(spot)
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(futures.id))
    await contracts.bind_contract(_binding(stopped.id))
    report = await build_futures_books_report(
        execution=store,
        journal=None,
        contracts=contracts,
        observations=_Latest(),
        policy=_policy(),
        now=_AT,
    )
    assert report.report_kind == "futures_books"
    assert {book.deployment_id for book in report.payload.books} == {futures.id, stopped.id}
    assert report.payload.committed_paper_cash_usd == "50"
    assert report.payload.paper_capital_usd == "100000"
    assert report.payload.futures_policy_set is True
    assert report.payload.live_supported is False
    assert report.components[0].reason_code == "OK"
    assert report.overall_status is ReportStatus.HEALTHY


@pytest.mark.anyio
async def test_the_report_degrades_on_unknown_evidence_and_fails_on_storage() -> None:
    """An unbound active book is degraded evidence; unreadable storage is a failed read."""
    store = InMemoryExecutionStore()
    await store.create_deployment(_deployment(cash="50"))
    degraded = await build_futures_books_report(
        execution=store,
        journal=None,
        contracts=InMemoryFuturesContractStore(),
        observations=None,
        policy=_policy(),
        now=_AT,
    )
    assert degraded.components[0].reason_code == "FUTURES_EVIDENCE_UNKNOWN"
    assert degraded.overall_status is ReportStatus.DEGRADED
    failed = await build_futures_books_report(
        execution=_FailingExecution(),
        journal=None,
        contracts=None,
        observations=None,
        policy=None,
        now=_AT,
    )
    assert failed.components[0].reason_code == "STORE_UNAVAILABLE"
    assert failed.payload.books == ()
    assert failed.payload.futures_policy_set is None
    empty = await build_futures_books_report(
        execution=InMemoryExecutionStore(),
        journal=None,
        contracts=None,
        observations=None,
        policy=compiled_default_risk_policy(),
        now=_AT,
    )
    assert empty.components[0].reason_code == "NO_FUTURES_BOOKS"
    assert empty.payload.futures_policy_set is False


class _BrokenPolicies(InMemoryRiskPolicyStore):
    """A policy store whose database is down."""

    async def load_active(self) -> ActiveRiskPolicy:
        """Fail like PostgreSQL."""
        raise RiskPolicyStoreError("Risk-policy storage is unavailable.")


@pytest.mark.anyio
async def test_an_unreadable_policy_is_none_not_the_compiled_default() -> None:
    """Only a missing store falls back to the compiled default."""
    assert await effective_policy_or_none(_BrokenPolicies()) is None
    assert await effective_policy_or_none(None) == compiled_default_risk_policy()


class _BrokenCatalog(_Latest):
    """A futures catalog whose database is down."""

    async def latest_instrument(
        self, product_id: str
    ) -> tuple[FuturesInstrumentObservation, datetime] | None:
        """Fail like an unreachable database."""
        del product_id
        raise RuntimeError("down")


@pytest.mark.anyio
async def test_the_async_view_reads_the_binding_and_survives_a_failing_catalog() -> None:
    """A catalog read failure leaves margin unknown rather than failing the view."""
    snapshot = _snapshot(side=PositionSide.SHORT, cash="150")
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(snapshot.deployment.id))
    book = await futures_book_payload(
        snapshot,
        journal=None,
        contracts=contracts,
        observations=_BrokenCatalog(),
        policy=_policy(),
        now=_AT,
    )
    assert book.contracts == "100"
    assert book.unknown == ("mark", "margin_rates")
    assert book.entry_blocks == ("FUTURES_MARGIN_UNKNOWN",)
