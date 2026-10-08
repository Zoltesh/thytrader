"""Portfolio reports qualify run performance and exposure with economic evidence."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest

from tests.operator_diagnostics.test_projection_observability import _fill, _order
from tests.portfolios.runtime_support import operator, portfolio, world
from thytrader.portfolios.briefing import ManagerBriefing, build_manager_briefing
from thytrader.portfolios.runtime_views import PortfolioDeploymentResponse, deployment_response
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentStatus,
    Fill,
    InstrumentRuntime,
    OrderStatus,
    Position,
    RuntimePhase,
)

if TYPE_CHECKING:
    from tests.portfolios.runtime_support import World
    from thytrader.portfolios.runtime import PortfolioDeploymentSnapshot

pytestmark = pytest.mark.anyio
_Fault = Literal["runtime", "unprojected", "canceled_partial", "filled_unpublished", "unapplied"]


async def _portfolio_snapshot(
    fault: _Fault,
) -> tuple[World, PortfolioDeploymentSnapshot]:
    """Read actual in-memory sleeve rows after prior reported profit and a later fault."""
    state = world()
    aggregate, _ = await portfolio(state)
    pid = aggregate.portfolio.portfolio_id
    await state.runtime.start(pid, revision=aggregate.portfolio.revision, context=operator())
    first, second = await state.tagged(pid)
    await state.mark_equity(first.id, pnl="50")
    await state.mark_equity(second.id, pnl="20")
    book = (await state.execution.get_deployment(first.id)).deployment
    if fault == "runtime":
        await state.execution.save_position(
            Position(
                deployment_id=book.id,
                product_id="BTC-USDC",
                quantity=Decimal("1"),
                entry_price=Decimal("100"),
                stop_price=Decimal("90"),
                target_price=Decimal("120"),
                entered_bar=book.created_at,
                updated_at=book.updated_at,
            ),
            deployment_id=book.id,
            product_id="BTC-USDC",
        )
        for product in ("BTC-USDC", "ETH-USDC"):
            await state.execution.save_instrument_runtime(
                InstrumentRuntime(product_id=product, phase=RuntimePhase.OPEN),
                deployment_id=book.id,
            )
        await state.execution.save_deployment(replace(book, phase=RuntimePhase.OPEN))
    else:
        order = await _order(
            state.execution,
            book,
            product="BTC-USDC",
            status=OrderStatus.CANCELED
            if fault in {"canceled_partial", "unapplied"}
            else OrderStatus.FILLED,
            filled="0.4" if fault in {"canceled_partial", "unapplied"} else "1",
            created_at=book.created_at + timedelta(seconds=1),
        )
        if fault in {"unprojected", "unapplied"}:
            await state.execution.save_fill(_fill(order, applied=fault == "unprojected"))
    snapshot = await state.runtime.snapshot(pid)
    # Membership reads are newest-first; this fixture names books in sleeve creation order.
    return state, replace(
        snapshot,
        tagged=tuple(sorted(snapshot.tagged, key=lambda row: row.created_at)),
        snapshots=tuple(sorted(snapshot.snapshots, key=lambda row: row.deployment.created_at)),
    )


@pytest.mark.parametrize(
    "fault", ["runtime", "unprojected", "canceled_partial", "filled_unpublished", "unapplied"]
)
async def test_unresolved_portfolio_totals_do_not_inherit_prior_profit(fault: _Fault) -> None:
    """Neither persisted equity nor a profitable prior sleeve repairs current book evidence."""
    state, snapshot = await _portfolio_snapshot(fault)
    view = deployment_response(snapshot, pending_proposals=0)
    first = view.sleeves[0].deployment
    second = view.sleeves[1].deployment
    assert first is not None and second is not None
    assert not first.accounting_complete
    assert first.performance_equity is first.net_pnl is first.exposure_quote is None
    assert first.return_fraction is first.drawdown_fraction is None
    assert first.allocated_capital == first.paper_starting_cash == "500"
    assert second.accounting_complete and second.net_pnl == "20"
    assert not view.breaker.accounting_complete and view.breaker.equity is None
    assert view.breaker.daily_pnl is view.breaker.drawdown_fraction is None
    assert view.breaker.day_open_equity == view.breaker.high_water_mark_equity == "1000"
    assert view.breaker.unresolved_deployment_ids == (first.deployment_id,)
    assert not view.exposure.accounting_complete
    assert view.exposure.total_quote is view.exposure.fraction_of_capital is None
    assert view.exposure.cap_quote == "1000"  # Stored limits/allocations are independent.
    if fault == "runtime":
        assets = {row.asset: row for row in view.exposure.assets}
        assert assets["BTC"].exposure_quote == "100"
        assert assets["ETH"].exposure_quote is assets["ETH"].fraction_of_capital is None
        assert first.books[0].quantity == "1" and first.books[0].protection.required_quantity == "1"
    briefing = await build_manager_briefing(
        snapshot, portfolios=state.portfolios, decisions=state.decisions
    )
    assert not briefing.performance.accounting_complete
    assert (
        briefing.performance.equity
        is briefing.performance.net_pnl
        is briefing.performance.return_fraction
        is None
    )
    assert briefing.permissions.order_authority is False
    assert briefing.journal and briefing.disclosures
    assert ManagerBriefing.model_validate_json(briefing.model_dump_json()) == briefing
    assert PortfolioDeploymentResponse.model_validate_json(view.model_dump_json()) == view


@pytest.mark.parametrize("status", [DeploymentStatus.STOPPED, DeploymentStatus.PAUSED])
async def test_previous_run_book_only_qualifies_current_exposure(status: DeploymentStatus) -> None:
    """Old run PnL is excluded, but occupied residuals are not free capacity."""
    state, snapshot = await _portfolio_snapshot("runtime")
    first = snapshot.tagged[0]
    started = snapshot.runtime.run_started_at
    assert started is not None
    await state.execution.save_deployment(
        replace(first, status=status, created_at=started - timedelta(days=1))
    )
    snapshot = await state.runtime.snapshot(snapshot.aggregate.portfolio.portfolio_id)
    view = deployment_response(snapshot, pending_proposals=0)
    assert view.breaker.accounting_complete and view.breaker.equity == "1020"
    assert not view.exposure.accounting_complete and view.exposure.total_quote is None
    assert view.exposure.unresolved_deployment_ids == (first.id,)


async def test_current_run_stopped_book_remains_in_performance_and_residual_scope() -> None:
    """Stopping an unresolved book neither erases its run history nor certifies flat exposure."""
    state, snapshot = await _portfolio_snapshot("runtime")
    first = snapshot.tagged[0]
    await state.execution.save_deployment(replace(first, status=DeploymentStatus.STOPPED))
    snapshot = await state.runtime.snapshot(snapshot.aggregate.portfolio.portfolio_id)
    view = deployment_response(snapshot, pending_proposals=0)
    assert not view.breaker.accounting_complete and not view.exposure.accounting_complete
    assert view.breaker.equity is view.exposure.total_quote is None


@pytest.mark.parametrize("partial", [False, True])
async def test_missing_or_focused_reads_do_not_certify_portfolio_totals(partial: bool) -> None:
    """Full expected membership is required independently of persisted equity counters."""
    _, snapshot = await _portfolio_snapshot("runtime")
    first, second = snapshot.snapshots
    snapshots = (replace(first, accounting_complete=False), second) if partial else (second,)
    view = deployment_response(replace(snapshot, snapshots=snapshots), pending_proposals=0)
    assert not view.breaker.accounting_complete and not view.exposure.accounting_complete
    assert view.breaker.equity is view.exposure.total_quote is None
    first_view = view.sleeves[0].deployment
    assert first_view is not None and first_view.net_pnl is None
    if not partial:
        assert first_view.open_books is None


@pytest.mark.parametrize("scope", ["other_portfolio", "other_mode"])
async def test_unrelated_unresolved_books_do_not_poison_current_portfolio(scope: str) -> None:
    """Only this portfolio and mode contribute to run and exposure completeness."""
    _, snapshot = await _portfolio_snapshot("runtime")
    fault, good = snapshot.snapshots
    clean = replace(
        fault,
        instrument_runtimes=(InstrumentRuntime(product_id="BTC-USDC", phase=RuntimePhase.OPEN),),
    )
    unrelated_row = replace(
        fault.deployment,
        id=uuid4(),
        portfolio_id=uuid4() if scope == "other_portfolio" else fault.deployment.portfolio_id,
        mode=DeploymentMode.LIVE if scope == "other_mode" else fault.deployment.mode,
    )
    unrelated = replace(fault, deployment=unrelated_row)
    mixed = replace(
        snapshot, snapshots=(clean, good, unrelated), tagged=(*snapshot.tagged, unrelated_row)
    )
    view = deployment_response(mixed, pending_proposals=0)
    assert view.breaker.accounting_complete and view.breaker.equity == "1070"
    assert view.exposure.accounting_complete and view.exposure.total_quote == "100"


async def test_other_quote_runtime_does_not_certify_or_pollute_quote_scoped_exposure() -> None:
    """A USDC exposure subtotal remains independent of unknown USD inventory, without FX."""
    _, snapshot = await _portfolio_snapshot("runtime")
    first, second = snapshot.snapshots
    scoped = replace(
        first,
        instrument_runtimes=(
            InstrumentRuntime(product_id="BTC-USDC", phase=RuntimePhase.OPEN),
            InstrumentRuntime(product_id="ETH-USD", phase=RuntimePhase.OPEN),
        ),
    )
    view = deployment_response(replace(snapshot, snapshots=(scoped, second)), pending_proposals=0)
    assert not view.breaker.accounting_complete
    assert view.exposure.accounting_complete and view.exposure.total_quote == "100"
    assert all(row.asset != "ETH" or row.exposure_quote == "0" for row in view.exposure.assets)


async def test_complete_mixed_quote_inventory_does_not_become_one_pnl_currency() -> None:
    """Even resolved quantities cannot define a cross-currency sleeve/run PnL or exposure sum."""
    _, snapshot = await _portfolio_snapshot("runtime")
    first, second = snapshot.snapshots
    other_quote = replace(first.positions[0], product_id="ETH-USD", quantity=Decimal("3"))
    mixed = replace(
        first,
        positions=(*first.positions, other_quote),
        instrument_runtimes=(
            InstrumentRuntime(product_id="BTC-USDC", phase=RuntimePhase.OPEN),
            InstrumentRuntime(product_id="ETH-USD", phase=RuntimePhase.OPEN),
        ),
    )
    assert ledger_from_snapshot(mixed).accounting_complete
    view = deployment_response(replace(snapshot, snapshots=(mixed, second)), pending_proposals=0)
    assert not view.breaker.accounting_complete and view.breaker.equity is None
    assert view.sleeves[0].deployment is not None
    assert view.sleeves[0].deployment.net_pnl is view.sleeves[0].deployment.exposure_quote is None
    assert view.exposure.accounting_complete and view.exposure.total_quote == "100"


async def test_unassignable_fill_cannot_certify_other_asset_totals() -> None:
    """An orphan unapplied fill may belong to any asset, not only the primary product."""
    _, snapshot = await _portfolio_snapshot("runtime")
    first, second = snapshot.snapshots
    fill = Fill(
        id=uuid4(),
        deployment_id=first.deployment.id,
        order_id=uuid4(),
        venue_fill_id="orphan",
        price=Decimal("100"),
        quantity=Decimal("1"),
        fee=Decimal("0"),
        filled_at=first.deployment.created_at,
    )
    unknown = replace(first, fills=(fill,))
    view = deployment_response(replace(snapshot, snapshots=(unknown, second)), pending_proposals=0)
    assert not view.exposure.accounting_complete
    assert view.exposure.total_quote is None
    assert all(row.exposure_quote is None for row in view.exposure.assets)


async def test_flat_runtime_positive_control_restores_current_totals() -> None:
    """Real persisted FLAT ETH evidence restores totals while leaving its BTC sibling occupied."""
    state, snapshot = await _portfolio_snapshot("runtime")
    await state.execution.save_instrument_runtime(
        InstrumentRuntime(product_id="ETH-USDC", phase=RuntimePhase.FLAT),
        deployment_id=snapshot.tagged[0].id,
    )
    view = deployment_response(
        await state.runtime.snapshot(snapshot.aggregate.portfolio.portfolio_id), pending_proposals=0
    )
    assert view.breaker.accounting_complete and view.breaker.equity == "1070"
    assert view.exposure.accounting_complete and view.exposure.total_quote == "100"
    assert view.sleeves[0].deployment is not None and view.sleeves[0].deployment.net_pnl == "50"
