"""Advisory readiness and venue-wide reconciliation (ADR 0114)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from thytrader.exchanges.fees import FeeProfile
from thytrader.exchanges.models import ExchangeBalance, ExchangeOpenOrder
from thytrader.exchanges.read_errors import (
    ExchangeReadError,
    ExchangeReadFailure,
    ExchangeReadFailureKind,
    ExchangeReadOperation,
)
from thytrader.execution.ids import uuid7
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
)
from thytrader.operator.cli import _parser
from thytrader.operator.readiness import (
    ReadinessPayload,
    ReadinessReport,
    ReadinessSeverity,
    build_readiness_report,
)
from thytrader.operator.venue_reconciliation import (
    VenueReconciliationPayload,
    VenueReconciliationReport,
    VenueSeverity,
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
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore

if TYPE_CHECKING:
    from thytrader.risk.models import RiskPolicyDefinition

_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


class ScriptedExchange:
    """Hermetic venue reads. Failures replace a listing; they never return a partial one."""

    def __init__(
        self,
        balances: tuple[ExchangeBalance, ...] = (),
        orders: tuple[ExchangeOpenOrder, ...] = (),
        *,
        maker: str = "0.005",
        taker: str = "0.009",
        fail_balances: Exception | None = None,
        fail_orders: Exception | None = None,
        fail_fees: Exception | None = None,
    ) -> None:
        """Store the scripted listing and optional typed failures."""
        self.balances = balances
        self.orders = orders
        self.maker = Decimal(maker)
        self.taker = Decimal(taker)
        self.fail_balances = fail_balances
        self.fail_orders = fail_orders
        self.fail_fees = fail_fees

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Return the scripted balances or fail the whole listing."""
        if self.fail_balances is not None:
            raise self.fail_balances
        return self.balances

    async def get_permissions(self) -> tuple[str, ...]:
        """Report view without leaking an account id."""
        return ("view",)

    async def get_usd_price(self, currency: str) -> Decimal | None:
        """Stable quotes are par; other assets are unused by these reports."""
        del currency
        return Decimal("1")

    async def get_fee_profile(self) -> FeeProfile:
        """Return account fee evidence, or fail without inventing rates."""
        if self.fail_fees is not None:
            raise self.fail_fees
        return FeeProfile(
            taker_fee_rate=self.taker,
            maker_fee_rate=self.maker,
            usd_volume_30d=Decimal("1000"),
            fee_tier="observed",
            as_of=_NOW,
            source="coinbase",
        )

    async def list_open_orders(self) -> tuple[ExchangeOpenOrder, ...]:
        """Return the scripted open orders or fail the whole listing."""
        if self.fail_orders is not None:
            raise self.fail_orders
        return self.orders


class _Portfolios:
    """One in-memory portfolio aggregate for readiness scope tests."""

    def __init__(self, aggregate: PortfolioAggregate) -> None:
        """Keep the aggregate and an unlatched runtime."""
        self.aggregate = aggregate
        self.runtime = PortfolioRuntimeState(portfolio_id=aggregate.portfolio.portfolio_id)

    async def list_page(self, *, limit: int, offset: int) -> PortfolioPage:
        """Return the single portfolio."""
        del limit, offset
        return PortfolioPage(portfolios=(self.aggregate,), total=1)

    async def get(self, portfolio_id: UUID) -> PortfolioAggregate:
        """Return the aggregate when the id matches."""
        assert portfolio_id == self.aggregate.portfolio.portfolio_id
        return self.aggregate

    async def runtime_state(self, portfolio_id: UUID) -> PortfolioRuntimeState:
        """Return the unlatched runtime state."""
        del portfolio_id
        return self.runtime


def _balance(currency: str, available: str, hold: str = "0") -> ExchangeBalance:
    """One exact venue balance row."""
    return ExchangeBalance(currency, currency, Decimal(available), Decimal(hold))


def _deployment(
    *,
    mode: DeploymentMode = DeploymentMode.LIVE,
    product_id: str = "BTC-USDC",
    allocated: str | None = None,
    paper_cash: str | None = None,
    maker: str | None = None,
    taker: str | None = None,
    portfolio_id: UUID | None = None,
    status: DeploymentStatus = DeploymentStatus.RUNNING,
) -> Deployment:
    """One book with explicit capital and optional paper fee assumptions."""
    return Deployment(
        id=uuid7(_NOW),
        strategy_fingerprint="sha256:" + "a" * 64,
        strategy_id=UUID(int=9),
        product_id=product_id,
        mode=mode,
        status=status,
        cash=Decimal("0"),
        phase=RuntimePhase.FLAT,
        created_at=_NOW,
        updated_at=_NOW,
        paper_starting_cash=None if paper_cash is None else Decimal(paper_cash),
        paper_maker_fee_rate=None if maker is None else Decimal(maker),
        paper_taker_fee_rate=None if taker is None else Decimal(taker),
        allocated_capital=None if allocated is None else Decimal(allocated),
        portfolio_id=portfolio_id,
        strategy_name="sleeve",
    )


async def _seed(store: InMemoryExecutionStore, deployment: Deployment) -> Deployment:
    """Persist one deployment."""
    return await store.create_deployment(deployment)


def _policy(**updates: str) -> RiskPolicyDefinition:
    """Published policy with exact decimal overrides."""
    base = compiled_default_risk_policy()
    return base.model_copy(update={"version": 2, **updates})


def _codes(report: ReadinessReport | VenueReconciliationReport) -> set[str]:
    """Finding codes from either report payload."""
    payload = report.payload
    if isinstance(payload, ReadinessPayload):
        return {finding.reason_code for finding in payload.findings}
    assert isinstance(payload, VenueReconciliationPayload)
    return {finding.reason_code for finding in payload.findings}


def _json_keys(rendered: str) -> set[str]:
    """Every object key in a serialized report, to assert identifier keys stay absent."""
    keys: set[str] = set()

    def walk(node: Any) -> None:  # Any is unavoidable: json.loads returns dynamic values.
        if isinstance(node, dict):
            for key, value in node.items():
                keys.add(key)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(json.loads(rendered))
    return keys


def test_allocation_overcommitment_is_advisory_not_an_exposure_violation() -> None:
    """Eight 40 allocations against an 80 cap warn without claiming a violation."""

    async def scenario() -> None:
        store = InMemoryExecutionStore()
        for _index in range(8):
            await _seed(store, _deployment(allocated="40"))
        policies = InMemoryRiskPolicyStore()
        await policies.publish(_policy(max_portfolio_exposure_quote="80"))
        report = await build_readiness_report(
            portfolio=PortfolioService(ScriptedExchange((_balance("USDC", "320"),)), demo=False),
            execution=store,
            risk_policies=policies,
            portfolios=None,
        )
        assert report.payload.account is not None
        account = report.payload.account
        assert account.quote_currency == "USDC"
        assert account.venue_available_quote == "320"
        assert account.capital_base == "320"
        assert account.effective_exposure_cap == "80"
        assert account.current_exposure == "0"
        assert account.remaining_entry_capacity == "80"
        assert account.enforcement == "advisory_only"
        codes = _codes(report)
        assert "ALLOCATION_OVERCOMMITMENT" in codes
        assert "ACCOUNT_EXPOSURE_CAP_EXCEEDED" not in codes
        finding = next(
            item
            for item in report.payload.findings
            if item.reason_code == "ALLOCATION_OVERCOMMITMENT"
        )
        assert finding.severity is ReadinessSeverity.ADVISORY
        assert "320" in finding.detail
        assert "80" in finding.detail

    asyncio.run(scenario())


def test_actual_exposure_above_the_cap_is_a_violation() -> None:
    """Marked inventory above the effective cap is not merely advisory."""

    async def scenario() -> None:
        store = InMemoryExecutionStore()
        deployment = await _seed(store, _deployment(allocated="40"))
        await store.save_position(
            Position(
                deployment_id=deployment.id,
                quantity=Decimal("1"),
                entry_price=Decimal("100"),
                stop_price=Decimal("90"),
                target_price=None,
                entered_bar=_NOW,
                updated_at=_NOW,
                side=PositionSide.LONG,
                product_id="BTC-USDC",
            ),
            deployment_id=deployment.id,
            product_id="BTC-USDC",
        )
        policies = InMemoryRiskPolicyStore()
        await policies.publish(
            _policy(max_portfolio_exposure_quote="80", per_product_max_exposure_fraction="1")
        )
        report = await build_readiness_report(
            portfolio=PortfolioService(ScriptedExchange((_balance("USDC", "50"),)), demo=False),
            execution=store,
            risk_policies=policies,
            portfolios=None,
        )
        account = report.payload.account
        assert account is not None
        assert account.capital_base == "150"
        assert account.managed_long_inventory_cost == "100"
        assert account.current_exposure == "100"
        assert account.effective_exposure_cap == "80"
        assert "ACCOUNT_EXPOSURE_CAP_EXCEEDED" in _codes(report)
        assert report.overall_status.value == "degraded"

    asyncio.run(scenario())


def test_quote_currencies_are_not_summed_and_usd_books_stay_disclosed() -> None:
    """A USD book is excluded from the USDC account total instead of being relabeled."""

    async def scenario() -> None:
        store = InMemoryExecutionStore()
        await _seed(store, _deployment(product_id="BTC-USD", allocated="40"))
        await _seed(store, _deployment(allocated="40"))
        policies = InMemoryRiskPolicyStore()
        await policies.publish(_policy(max_portfolio_exposure_quote="1000"))
        report = await build_readiness_report(
            portfolio=PortfolioService(
                ScriptedExchange((_balance("USDC", "320"), _balance("USD", "1000"))),
                demo=False,
            ),
            execution=store,
            risk_policies=policies,
            portfolios=None,
        )
        assert "QUOTE_CURRENCY_MISMATCH" in _codes(report)
        account = report.payload.account
        assert account is not None
        assert account.quote_currency == "USDC"
        assert account.venue_available_quote == "320"
        quotes = {row.quote_currency: row.venue_total for row in report.payload.venue_quotes}
        assert quotes["USD"] == "1000"
        assert quotes["USDC"] == "320"

    asyncio.run(scenario())


def test_duplicate_quote_balances_are_summed() -> None:
    """Two USDC wallet rows add; neither overwrites the other."""

    async def scenario() -> None:
        policies = InMemoryRiskPolicyStore()
        await policies.publish(_policy())
        report = await build_readiness_report(
            portfolio=PortfolioService(
                ScriptedExchange((_balance("USDC", "20"), _balance("USDC", "5", "1"))),
                demo=False,
            ),
            execution=InMemoryExecutionStore(),
            risk_policies=policies,
            portfolios=None,
        )
        account = report.payload.account
        assert account is not None
        assert account.venue_available_quote == "25"
        row = next(item for item in report.payload.venue_quotes if item.quote_currency == "USDC")
        assert row.venue_hold == "1"
        assert row.venue_total == "26"

    asyncio.run(scenario())


def test_unknown_venue_balance_does_not_invent_capacity() -> None:
    """A failed balance read leaves caps unknown and does not treat exposure as zero-ok."""

    async def scenario() -> None:
        store = InMemoryExecutionStore()
        await _seed(store, _deployment(allocated="40"))
        failure = ExchangeReadError(
            ExchangeReadFailure(
                operation=ExchangeReadOperation.BALANCES,
                kind=ExchangeReadFailureKind.TIMEOUT,
            )
        )
        report = await build_readiness_report(
            portfolio=PortfolioService(ScriptedExchange(fail_balances=failure), demo=False),
            execution=store,
            risk_policies=InMemoryRiskPolicyStore(),
            portfolios=None,
        )
        account = report.payload.account
        assert account is not None
        assert account.capital_base is None
        assert account.effective_exposure_cap is None
        assert account.remaining_entry_capacity is None
        assert account.venue_read_failure is not None
        assert account.venue_read_failure.operation is ExchangeReadOperation.BALANCES
        finding = next(
            item for item in report.payload.findings if item.reason_code == "VENUE_BALANCE_UNKNOWN"
        )
        assert finding.severity is ReadinessSeverity.UNKNOWN
        assert "ACCOUNT_EXPOSURE_CAP_EXCEEDED" not in _codes(report)

    asyncio.run(scenario())


def test_older_paper_fee_defaults_are_more_optimistic_than_account_evidence() -> None:
    """Paper 0.001/0.002 against account 0.005/0.009 is an advisory, not a policy change."""

    async def scenario() -> None:
        store = InMemoryExecutionStore()
        await _seed(
            store,
            _deployment(mode=DeploymentMode.PAPER, paper_cash="100", product_id="ETH-USDC"),
        )
        report = await build_readiness_report(
            portfolio=PortfolioService(ScriptedExchange(), demo=False),
            execution=store,
            risk_policies=InMemoryRiskPolicyStore(),
            portfolios=None,
        )
        evidence = report.payload.fee_evidence
        assert evidence.account_maker_fee_rate == "0.005"
        assert evidence.account_taker_fee_rate == "0.009"
        assert evidence.paper_books_defaulting_rates == 1
        assert len(evidence.optimistic_books) == 1
        gap = evidence.optimistic_books[0]
        assert gap.assumed_maker_fee_rate == "0.001"
        assert gap.assumed_taker_fee_rate == "0.002"
        assert gap.maker_gap == "0.004"
        assert gap.taker_gap == "0.007"
        assert "PAPER_FEE_ASSUMPTION_MORE_OPTIMISTIC" in _codes(report)

    asyncio.run(scenario())


def test_absent_fee_evidence_is_not_invented() -> None:
    """A failed fee read lists paper assumptions and refuses an optimism verdict."""

    async def scenario() -> None:
        store = InMemoryExecutionStore()
        await _seed(store, _deployment(mode=DeploymentMode.PAPER, paper_cash="10"))
        failure = ExchangeReadError(
            ExchangeReadFailure(
                operation=ExchangeReadOperation.FEES,
                kind=ExchangeReadFailureKind.HTTP,
                http_status=503,
            )
        )
        report = await build_readiness_report(
            portfolio=PortfolioService(ScriptedExchange(fail_fees=failure), demo=False),
            execution=store,
            risk_policies=InMemoryRiskPolicyStore(),
            portfolios=None,
        )
        evidence = report.payload.fee_evidence
        assert evidence.unavailable_reason == "read_failure"
        assert evidence.account_maker_fee_rate is None
        assert evidence.optimistic_books == ()
        assert evidence.read_failure is not None
        assert evidence.read_failure.http_status == 503
        assert "FEE_EVIDENCE_UNAVAILABLE" in _codes(report)
        assert "PAPER_FEE_ASSUMPTION_MORE_OPTIMISTIC" not in _codes(report)

    asyncio.run(scenario())


def test_demo_fee_evidence_is_not_compared() -> None:
    """Demo rates are labeled and never used to judge paper assumptions."""

    async def scenario() -> None:
        store = InMemoryExecutionStore()
        await _seed(store, _deployment(mode=DeploymentMode.PAPER, paper_cash="10"))
        report = await build_readiness_report(
            portfolio=PortfolioService(ScriptedExchange(), demo=True),
            execution=store,
            risk_policies=InMemoryRiskPolicyStore(),
            portfolios=None,
        )
        assert report.payload.fee_evidence.demo is True
        assert report.payload.fee_evidence.optimistic_books == ()
        assert "PAPER_FEE_ASSUMPTION_MORE_OPTIMISTIC" not in _codes(report)

    asyncio.run(scenario())


def test_portfolio_cap_can_bind_tighter_than_the_account_breaker() -> None:
    """A 10 quote portfolio stop is named tighter than a loose account fraction."""

    async def scenario() -> None:
        portfolio_id = uuid7(_NOW)
        store = InMemoryExecutionStore()
        deployment = await _seed(store, _deployment(allocated="80", portfolio_id=portfolio_id))
        await store.save_position(
            Position(
                deployment_id=deployment.id,
                quantity=Decimal("1"),
                entry_price=Decimal("100"),
                stop_price=Decimal("90"),
                target_price=None,
                entered_bar=_NOW,
                updated_at=_NOW,
                product_id="BTC-USDC",
            ),
            deployment_id=deployment.id,
            product_id="BTC-USDC",
        )
        aggregate = PortfolioAggregate(
            portfolio=Portfolio(
                portfolio_id=portfolio_id,
                name="Exploratory",
                mode="live",
                quote_currency="USDC",
                capital_quote="320",
                cash_reserve_fraction="0",
                limits=PortfolioLimits(
                    max_total_exposure_fraction="0.25",
                    max_per_asset_fraction="0.2",
                    daily_loss_quote="10",
                ),
                manager=ManagerSettings(),
                revision=1,
                created_at=_NOW,
                updated_at=_NOW,
            ),
            sleeves=(),
        )
        policies = InMemoryRiskPolicyStore()
        await policies.publish(_policy(max_portfolio_exposure_quote="500"))
        report = await build_readiness_report(
            portfolio=PortfolioService(ScriptedExchange((_balance("USDC", "320"),)), demo=False),
            execution=store,
            risk_policies=policies,
            portfolios=_Portfolios(aggregate),
            portfolio_id=portfolio_id,
        )
        assert report.payload.scope == "portfolio"
        section = report.payload.portfolios[0]
        assert section.total_exposure_cap == "80"
        assert section.current_total_exposure == "100"
        assert section.per_asset_cap == "64"
        assert section.daily_loss_quote_stop == "10"
        assert section.tighter_daily_breaker == "portfolio"
        assert section.account_daily_loss_cap == "420"
        assert "PORTFOLIO_EXPOSURE_CAP_EXCEEDED" in _codes(report)
        assert "PORTFOLIO_ASSET_EXPOSURE_CAP_EXCEEDED" in _codes(report)

    asyncio.run(scenario())


def test_foreign_holdings_are_not_errors_and_shortfalls_are() -> None:
    """Extra venue BTC is external; less venue BTC than managed books claim is a warning."""

    async def foreign() -> None:
        store = InMemoryExecutionStore()
        deployment = await _seed(store, _deployment())
        await store.save_position(
            Position(
                deployment_id=deployment.id,
                quantity=Decimal("0.5"),
                entry_price=Decimal("10"),
                stop_price=Decimal("9"),
                target_price=None,
                entered_bar=_NOW,
                updated_at=_NOW,
                product_id="BTC-USDC",
            ),
            deployment_id=deployment.id,
            product_id="BTC-USDC",
        )
        report = await build_venue_reconciliation_report(
            portfolio=PortfolioService(
                ScriptedExchange((_balance("BTC", "1.5"), _balance("USDC", "80"))),
                demo=False,
            ),
            execution=store,
        )
        row = next(item for item in report.payload.assets if item.currency == "BTC")
        assert row.classification == "external_inventory"
        assert row.foreign_quantity == "1"
        assert row.managed_net_quantity == "0.5"
        finding = next(
            item for item in report.payload.findings if item.reason_code == "EXTERNAL_INVENTORY"
        )
        assert finding.severity is VenueSeverity.INFO
        assert "never flattened" in finding.detail
        assert report.overall_status.value == "healthy"
        keys = _json_keys(report.model_dump_json())
        assert "account_id" not in keys
        assert "api_key" not in keys

    async def shortfall() -> None:
        store = InMemoryExecutionStore()
        deployment = await _seed(store, _deployment())
        await store.save_position(
            Position(
                deployment_id=deployment.id,
                quantity=Decimal("0.5"),
                entry_price=Decimal("10"),
                stop_price=Decimal("9"),
                target_price=None,
                entered_bar=_NOW,
                updated_at=_NOW,
                product_id="BTC-USDC",
            ),
            deployment_id=deployment.id,
            product_id="BTC-USDC",
        )
        report = await build_venue_reconciliation_report(
            portfolio=PortfolioService(ScriptedExchange((_balance("BTC", "0.1"),)), demo=False),
            execution=store,
        )
        row = next(item for item in report.payload.assets if item.currency == "BTC")
        assert row.classification == "managed_exceeds_venue"
        assert "MANAGED_INVENTORY_SHORTFALL" in _codes(report)
        assert report.overall_status.value == "degraded"

    asyncio.run(foreign())
    asyncio.run(shortfall())


def test_duplicate_balances_sum_and_incomplete_listings_stay_unknown() -> None:
    """Duplicate wallet rows add; a failed listing does not guess foreign or flat holdings."""

    async def duplicates() -> None:
        report = await build_venue_reconciliation_report(
            portfolio=PortfolioService(
                ScriptedExchange((_balance("BTC", "0.4"), _balance("BTC", "0.6"))),
                demo=False,
            ),
            execution=InMemoryExecutionStore(),
        )
        row = next(item for item in report.payload.assets if item.currency == "BTC")
        assert row.venue_quantity == "1"
        assert row.venue_rows == 2
        assert row.classification == "external_inventory"
        assert "DUPLICATE_BALANCE_ROWS" in _codes(report)

    async def incomplete() -> None:
        store = InMemoryExecutionStore()
        deployment = await _seed(store, _deployment())
        await store.save_position(
            Position(
                deployment_id=deployment.id,
                quantity=Decimal("1"),
                entry_price=Decimal("10"),
                stop_price=Decimal("9"),
                target_price=None,
                entered_bar=_NOW,
                updated_at=_NOW,
                product_id="BTC-USDC",
            ),
            deployment_id=deployment.id,
            product_id="BTC-USDC",
        )
        await store.save_order(
            Order(
                id=uuid7(_NOW),
                deployment_id=deployment.id,
                intent_id=uuid7(_NOW),
                client_order_id="entry-1",
                side=OrderSide.BUY,
                kind=OrderKind.POST_ONLY_LIMIT,
                quantity=Decimal("1"),
                status=OrderStatus.OPEN,
                created_at=_NOW,
                updated_at=_NOW,
                price=Decimal("10"),
                venue_order_id="managed-venue",
                product_id="BTC-USDC",
            )
        )
        failure = ExchangeReadError(
            ExchangeReadFailure(
                operation=ExchangeReadOperation.BALANCES,
                kind=ExchangeReadFailureKind.NETWORK,
            )
        )
        order_failure = ExchangeReadError(
            ExchangeReadFailure(
                operation=ExchangeReadOperation.OPEN_ORDERS,
                kind=ExchangeReadFailureKind.TIMEOUT,
            )
        )
        report = await build_venue_reconciliation_report(
            portfolio=PortfolioService(
                ScriptedExchange(fail_balances=failure, fail_orders=order_failure),
                demo=False,
            ),
            execution=store,
        )
        assert report.payload.balances_listing.status == "unavailable"
        assert report.payload.orders_listing.status == "unavailable"
        asset = next(item for item in report.payload.assets if item.currency == "BTC")
        assert asset.classification == "venue_unknown"
        assert asset.foreign_quantity is None
        assert asset.venue_quantity is None
        assert report.payload.orders.foreign is None
        assert report.payload.orders.orphan is None
        assert "VENUE_BALANCES_LISTING_INCOMPLETE" in _codes(report)
        assert "VENUE_ORDERS_LISTING_INCOMPLETE" in _codes(report)
        assert "EXTERNAL_INVENTORY" not in _codes(report)
        assert "MANAGED_ORDER_NOT_AT_VENUE" not in _codes(report)

    asyncio.run(duplicates())
    asyncio.run(incomplete())


def test_orphan_managed_orders_and_foreign_venue_orders_are_distinguished() -> None:
    """A missing managed order warns; a venue order nobody manages is only disclosed."""

    async def scenario() -> None:
        store = InMemoryExecutionStore()
        deployment = await _seed(store, _deployment())
        await store.save_order(
            Order(
                id=uuid7(_NOW),
                deployment_id=deployment.id,
                intent_id=uuid7(_NOW),
                client_order_id="entry-1",
                side=OrderSide.BUY,
                kind=OrderKind.POST_ONLY_LIMIT,
                quantity=Decimal("1"),
                status=OrderStatus.OPEN,
                created_at=_NOW,
                updated_at=_NOW,
                price=Decimal("10"),
                venue_order_id="managed-missing",
                product_id="BTC-USDC",
            )
        )
        report = await build_venue_reconciliation_report(
            portfolio=PortfolioService(
                ScriptedExchange(
                    orders=(
                        ExchangeOpenOrder(
                            venue_order_id="foreign-1",
                            product_id="ETH-USDC",
                            side="sell",
                            status="OPEN",
                        ),
                    )
                ),
                demo=False,
            ),
            execution=store,
        )
        assert report.payload.orders.listing.status == "complete"
        assert report.payload.orders.orphan is not None
        assert report.payload.orders.orphan[0].venue_order_id == "managed-missing"
        assert report.payload.orders.foreign is not None
        assert report.payload.orders.foreign[0].venue_order_id == "foreign-1"
        assert "MANAGED_ORDER_NOT_AT_VENUE" in _codes(report)
        foreign = next(
            item for item in report.payload.findings if item.reason_code == "EXTERNAL_OPEN_ORDERS"
        )
        assert foreign.severity is VenueSeverity.INFO
        assert "not cancelled" in foreign.detail

    asyncio.run(scenario())


def test_operator_parser_exposes_readiness_commands() -> None:
    """The read-only CLI names both reports and their scope flags."""
    readiness = _parser().parse_args(
        ["readiness", "--deployment-id", str(UUID(int=1)), "--portfolio-id", str(UUID(int=2))]
    )
    assert readiness.command == "readiness"
    assert readiness.deployment_id == str(UUID(int=1))
    venue = _parser().parse_args(["venue-reconciliation"])
    assert venue.command == "venue-reconciliation"


def test_reports_reject_unknown_payload_fields() -> None:
    """Public payloads stay strict."""
    with pytest.raises(ValueError):
        ReadinessPayload.model_validate({"scope": "fleet", "invented": True})
