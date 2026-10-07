"""Lead-review regressions: quote isolation, complete managed scope, and truthful unknowns."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

import pytest

from tests.operator_diagnostics.test_readiness_preflight import (
    _NOW,
    ScriptedExchange,
    _balance,
    _codes,
    _deployment,
    _policy,
    _seed,
)
from thytrader.exchanges.models import ExchangeOpenOrder
from thytrader.execution.ids import uuid7
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    DeploymentStatus,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
)
from thytrader.execution.store import DisabledExecutionStore
from thytrader.operator.readiness import build_readiness_report
from thytrader.operator.venue_reconciliation import (
    VenueReconciliationPayload,
    build_venue_reconciliation_report,
)
from thytrader.portfolio.service import PortfolioService
from thytrader.portfolios.models import (
    ManagerSettings,
    Portfolio,
    PortfolioAggregate,
    PortfolioLimits,
    PortfolioPage,
    PortfolioRuntimeState,
)
from thytrader.risk.store import InMemoryRiskPolicyStore

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.execution.models import Deployment, DeploymentSnapshot


class _UnreadableStore(InMemoryExecutionStore):
    """A listed book can become unreadable; listing failures are separate evidence."""

    def __init__(self, *, fail_list: bool = False) -> None:
        """Start hermetic storage with opt-in read failures."""
        super().__init__()
        self.missing: set[UUID] = set()
        self.fail_list = fail_list

    async def list_deployments(
        self, *, limit: int | None = None, offset: int = 0
    ) -> tuple[Deployment, ...]:
        """Fail the fleet read when requested, never return a fabricated empty fleet."""
        if self.fail_list:
            raise OSError("synthetic storage failure")
        return await super().list_deployments(limit=limit, offset=offset)

    async def get_deployment(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Fail selected snapshot reads without changing the persisted inventory."""
        if deployment_id in self.missing:
            raise OSError("synthetic snapshot failure")
        return await super().get_deployment(deployment_id)


class _Directory:
    """Hermetic portfolio directory with independent aggregate/runtime read failures."""

    def __init__(
        self,
        aggregates: tuple[PortfolioAggregate, ...],
        *,
        fail: Literal["get", "runtime"] | None = None,
    ) -> None:
        """Keep exact aggregates and a failure selector."""
        self.aggregates = aggregates
        self.fail = fail

    async def list_page(self, *, limit: int, offset: int) -> PortfolioPage:
        """Respect pagination so a 101-portfolio fleet exposes the report's 100-row bound."""
        return PortfolioPage(
            portfolios=self.aggregates[offset : offset + limit], total=len(self.aggregates)
        )

    async def get(self, portfolio_id: UUID) -> PortfolioAggregate:
        """Read one aggregate or fail; omission cannot leave the report healthy."""
        if self.fail == "get":
            raise OSError("synthetic aggregate failure")
        return next(item for item in self.aggregates if item.portfolio.portfolio_id == portfolio_id)

    async def runtime_state(self, portfolio_id: UUID) -> PortfolioRuntimeState:
        """Return observed empty runtime or fail, never invent a reset on failure."""
        if self.fail == "runtime":
            raise OSError("synthetic runtime failure")
        return PortfolioRuntimeState(portfolio_id=portfolio_id)


def _aggregate(
    *, mode: Literal["paper", "live"] = "live", quote: Literal["USD", "USDC"] = "USDC"
) -> PortfolioAggregate:
    """One 80-cap portfolio with a configured ten-quote daily stop."""
    return PortfolioAggregate(
        portfolio=Portfolio(
            portfolio_id=uuid7(_NOW),
            name="Scope regression",
            mode=mode,
            quote_currency=quote,
            capital_quote="320",
            cash_reserve_fraction="0",
            manager=ManagerSettings(),
            limits=PortfolioLimits(
                max_total_exposure_fraction="0.25",
                max_per_asset_fraction="0.25",
                daily_loss_quote="10",
            ),
            revision=1,
            created_at=_NOW,
            updated_at=_NOW,
        ),
        sleeves=(),
    )


async def _position(
    store: InMemoryExecutionStore, book: Deployment, product: str, cost: str
) -> None:
    """Persist exactly one unit so cost basis is the scripted quote amount."""
    await store.save_position(
        Position(
            deployment_id=book.id,
            quantity=Decimal("1"),
            entry_price=Decimal(cost),
            stop_price=Decimal(cost) / 2,
            target_price=None,
            entered_bar=_NOW,
            updated_at=_NOW,
            product_id=product,
        ),
        deployment_id=book.id,
        product_id=product,
    )


async def _order(
    store: InMemoryExecutionStore,
    book: Deployment,
    product: str,
    cost: str,
    *,
    venue_id: str | None = "venue-managed",
    status: OrderStatus = OrderStatus.OPEN,
) -> Order:
    """Persist a priced order without ever submitting it to a venue."""
    order = Order(
        id=uuid7(_NOW),
        deployment_id=book.id,
        intent_id=uuid7(_NOW),
        client_order_id=str(uuid7(_NOW)),
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        price=Decimal(cost),
        status=status,
        venue_order_id=venue_id,
        product_id=product,
        created_at=_NOW,
        updated_at=_NOW,
    )
    await store.save_order(order)
    return order


@pytest.mark.parametrize("quote", ["USD", "USDC"])
def test_actual_products_not_primary_book_quote_scope_account(
    quote: Literal["USD", "USDC"],
) -> None:
    """Mixed books split actual USD/USDC positions and reservations without FX or relabeling."""

    async def scenario() -> None:
        store = InMemoryExecutionStore()
        usd = await _seed(store, _deployment(product_id="BTC-USD"))
        usdc = await _seed(store, _deployment(product_id="BTC-USDC"))
        mixed = await _seed(store, _deployment(product_id="ETH-USDC", allocated="100"))
        await _position(store, usd, "BTC-USD", "20")
        await _position(store, usdc, "BTC-USDC", "1000")
        await _position(store, mixed, "ETH-USD", "50")
        await _position(store, mixed, "ETH-USDC", "2000")
        await _order(store, mixed, "ETH-USD", "5", venue_id="usd-working")
        await _order(store, mixed, "ETH-USDC", "500", venue_id="usdc-working")
        policies = InMemoryRiskPolicyStore()
        await policies.publish(
            _policy(
                quote_currency=quote,
                max_portfolio_exposure_fraction="0.5",
                per_product_max_exposure_fraction="0.25",
                max_portfolio_exposure_quote="10000",
            )
        )
        report = await build_readiness_report(
            portfolio=PortfolioService(
                ScriptedExchange((_balance("USD", "100"), _balance("USDC", "5000")))
            ),
            execution=store,
            risk_policies=policies,
            portfolios=_Directory(()),
        )
        account = report.payload.account
        assert account is not None and account.inventory.status == "complete"
        if quote == "USD":
            assert account.capital_base == "175"
            assert account.current_exposure == "75"
            assert account.effective_exposure_cap == "87.5"
            assert account.remaining_entry_capacity == "12.5"
            assert account.managed_long_inventory_cost == "70"
            assert account.working_buy_entry_reserved == "5"
        else:
            assert account.capital_base == "8500"
            assert account.current_exposure == "3500"
            assert account.effective_exposure_cap == "4250"
            assert account.remaining_entry_capacity == "750"
        assert all(row.product_id.endswith("-" + quote) for row in account.product_caps)
        assert all(not product.endswith("-" + quote) for product in account.excluded_products)
        assert "QUOTE_CURRENCY_MISMATCH" in _codes(report)
        mixed_row = next(row for row in report.payload.deployments if row.deployment_id == mixed.id)
        assert mixed_row.quote_currency is None
        assert mixed_row.exposure is None and mixed_row.inventory_cost is None
        assert mixed_row.allocated_capital is None and mixed_row.allocation_remaining is None
        assert {row.quote_currency: row.exposure for row in mixed_row.quote_exposures} == {
            "USD": "55",
            "USDC": "2500",
        }
        violations = [
            finding
            for finding in report.payload.findings
            if finding.reason_code == "PRODUCT_EXPOSURE_CAP_EXCEEDED"
        ]
        assert violations and all(f" {quote} " in finding.detail for finding in violations)

    asyncio.run(scenario())


def test_unreadable_live_book_nulls_account_and_scoped_inventory_totals() -> None:
    """A missing stopped book could retain inventory; known reads cannot prove free capacity."""

    async def scenario() -> None:
        store = _UnreadableStore()
        known = await _seed(store, _deployment())
        missing = await _seed(store, _deployment(status=DeploymentStatus.STOPPED))
        await _position(store, known, "BTC-USDC", "10")
        store.missing.add(missing.id)
        report = await build_readiness_report(
            portfolio=PortfolioService(ScriptedExchange((_balance("USDC", "100"),))),
            execution=store,
            risk_policies=None,
            portfolios=_Directory(()),
        )
        assert report.payload.inventory.status == "partial"
        account = report.payload.account
        assert account is not None
        assert account.inventory.expected_books == 2 and account.inventory.read_books == 1
        assert account.inventory.missing_deployment_ids == (missing.id,)
        assert account.current_exposure is None and account.capital_base is None
        assert (
            account.managed_long_inventory_cost is None
            and account.working_buy_entry_reserved is None
        )
        assert account.effective_exposure_cap is None and account.remaining_entry_capacity is None
        assert all(row.exposure is None and row.remaining is None for row in account.product_caps)
        assert report.overall_status.value == "degraded"

    asyncio.run(scenario())


@pytest.mark.parametrize("missing_sibling", [False, True])
def test_deployment_scope_includes_full_portfolio_sibling_inventory(missing_sibling: bool) -> None:
    """Scoped rows show A only; cap accounting includes B, or becomes unknown if B fails."""

    async def scenario() -> None:
        aggregate = _aggregate()
        store = _UnreadableStore()
        first = await _seed(store, _deployment(portfolio_id=aggregate.portfolio.portfolio_id))
        second = await _seed(store, _deployment(portfolio_id=aggregate.portfolio.portfolio_id))
        await _position(store, second, "BTC-USDC", "90")
        if missing_sibling:
            store.missing.add(second.id)
        report = await build_readiness_report(
            portfolio=PortfolioService(ScriptedExchange((_balance("USDC", "100"),))),
            execution=store,
            risk_policies=None,
            portfolios=_Directory((aggregate,)),
            deployment_id=first.id,
        )
        assert tuple(row.deployment_id for row in report.payload.deployments) == (first.id,)
        section = report.payload.portfolios[0]
        assert section.inventory.expected_books == 2
        if missing_sibling:
            assert (
                section.current_total_exposure is None and section.remaining_total_capacity is None
            )
            assert (
                section.inventory.status == "partial"
                and not report.payload.portfolio_scope_complete
            )
        else:
            assert (
                section.current_total_exposure == "90" and section.remaining_total_capacity == "-10"
            )
            assert section.asset_caps[0].exposure == "90"
            assert "PORTFOLIO_EXPOSURE_CAP_EXCEEDED" in _codes(report)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode,quote", [("paper", "USDC"), ("live", "USD")])
def test_breakers_with_different_economic_scopes_are_not_compared(
    mode: Literal["paper", "live"], quote: Literal["USD", "USDC"]
) -> None:
    """Paper money and other-quote daily stops cannot bind the live USDC account breaker."""

    async def scenario() -> None:
        aggregate = _aggregate(mode=mode, quote=quote)
        report = await build_readiness_report(
            portfolio=PortfolioService(ScriptedExchange((_balance("USDC", "100"),))),
            execution=InMemoryExecutionStore(),
            risk_policies=None,
            portfolios=_Directory((aggregate,)),
            portfolio_id=aggregate.portfolio.portfolio_id,
        )
        section = report.payload.portfolios[0]
        assert not section.account_breaker_comparable
        assert (
            section.account_daily_loss_cap is None
            and section.tighter_daily_breaker == "not_comparable"
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["get", "runtime", "truncated"])
def test_missing_portfolio_scope_cannot_report_healthy(
    failure: Literal["get", "runtime", "truncated"],
) -> None:
    """Failed aggregate/runtime reads and the fleet row bound are material incompleteness."""

    async def scenario() -> None:
        aggregates = tuple(_aggregate() for _ in range(101 if failure == "truncated" else 1))
        directory = _Directory(aggregates, fail=None if failure == "truncated" else failure)
        report = await build_readiness_report(
            portfolio=PortfolioService(ScriptedExchange((_balance("USDC", "100"),))),
            execution=InMemoryExecutionStore(),
            risk_policies=None,
            portfolios=directory,
        )
        assert report.overall_status.value == "degraded" and report.partial_result_warnings
        assert not report.payload.portfolio_scope_complete
        assert "READINESS_SCOPE_INCOMPLETE" in _codes(report)
        if failure == "runtime":
            section = report.payload.portfolios[0]
            assert not section.runtime_available and section.breaker_latched is None
            assert section.drawdown_stop_loss_allowance is None
        if failure == "truncated":
            assert len(report.payload.portfolios) == 100
            assert any("101" in warning for warning in report.partial_result_warnings)

    asyncio.run(scenario())


@pytest.mark.parametrize("store_kind", ["none", "disabled", "failed_list"])
def test_failed_managed_listing_builds_unavailable_envelopes(
    store_kind: Literal["none", "disabled", "failed_list"],
) -> None:
    """No store/list failures produce reports, not ValidationError or a healthy empty fleet."""

    async def scenario() -> None:
        service = PortfolioService(ScriptedExchange((_balance("BTC", "1"),)))
        execution = (
            None
            if store_kind == "none"
            else DisabledExecutionStore()
            if store_kind == "disabled"
            else _UnreadableStore(fail_list=True)
        )
        venue = await build_venue_reconciliation_report(portfolio=service, execution=execution)
        assert venue.overall_status.value == "failed"
        assert venue.payload.managed_listing.status == "unavailable"
        assert venue.payload.managed_books is None
        assert (
            venue.payload.balances_listing.status
            == venue.payload.orders_listing.status
            == "unavailable"
        )
        assert venue.payload.orders.foreign is None and venue.payload.orders.orphan is None
        assert VenueReconciliationPayload(observed_at=_NOW).orders.listing.status == "unavailable"
        readiness = await build_readiness_report(
            portfolio=service, execution=execution, risk_policies=None, portfolios=None
        )
        assert readiness.overall_status.value == "failed"
        assert (
            readiness.payload.inventory.status == "unavailable"
            and readiness.payload.account is None
        )

    asyncio.run(scenario())


def test_partial_managed_inventory_cannot_label_venue_assets_or_orders_foreign() -> None:
    """Even complete venue listings cannot classify against an incomplete managed side."""

    async def scenario() -> None:
        store = _UnreadableStore()
        known = await _seed(store, _deployment())
        unknown = await _seed(store, _deployment())
        await _position(store, known, "BTC-USDC", "10")
        await _order(store, known, "BTC-USDC", "10")
        store.missing.add(unknown.id)
        report = await build_venue_reconciliation_report(
            portfolio=PortfolioService(
                ScriptedExchange(
                    (_balance("BTC", "5"), _balance("ETH", "1"), _balance("USDC", "100")),
                    orders=(ExchangeOpenOrder("unclaimed", "ETH-USDC", "buy", "OPEN"),),
                )
            ),
            execution=store,
        )
        assert report.payload.managed_listing.status == "partial"
        assert report.payload.managed_listing.missing_deployment_ids == (unknown.id,)
        assert (
            report.payload.balances_listing.status
            == report.payload.orders_listing.status
            == "complete"
        )
        assert report.payload.managed_books is None
        assert all(
            row.classification == "managed_unknown" and row.foreign_quantity is None
            for row in report.payload.assets
        )
        assert all(row.managed_net_quantity is None for row in report.payload.assets)
        assert (
            next(row for row in report.payload.assets if row.currency == "BTC").venue_quantity
            == "5"
        )
        assert report.payload.quote_currencies[0].managed_working_buy_notional is None
        orders = report.payload.orders
        assert orders.venue_open == 1 and orders.matched is None
        assert orders.foreign is None and orders.orphan is None and orders.managed_working is None
        assert not {
            "EXTERNAL_INVENTORY",
            "EXTERNAL_OPEN_ORDERS",
            "MANAGED_ORDER_NOT_AT_VENUE",
        } & _codes(report)
        assert report.overall_status.value == "degraded"

    asyncio.run(scenario())


def test_pending_submit_matches_by_client_id_instead_of_becoming_foreign() -> None:
    """An ambiguous local submit with no venue ID still claims its durable client ID."""

    async def scenario() -> None:
        store = InMemoryExecutionStore()
        book = await _seed(store, _deployment())
        order = await _order(
            store, book, "BTC-USDC", "10", venue_id=None, status=OrderStatus.UNKNOWN
        )
        report = await build_venue_reconciliation_report(
            portfolio=PortfolioService(
                ScriptedExchange(
                    orders=(
                        ExchangeOpenOrder(
                            "observed-id",
                            "BTC-USDC",
                            "buy",
                            "CANCEL_QUEUED",
                            order.client_order_id,
                        ),
                    )
                )
            ),
            execution=store,
        )
        assert report.payload.orders.matched == 1
        assert report.payload.orders.foreign == () and report.payload.orders.orphan == ()
        assert "EXTERNAL_OPEN_ORDERS" not in _codes(report)

    asyncio.run(scenario())
