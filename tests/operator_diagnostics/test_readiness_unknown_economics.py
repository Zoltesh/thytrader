"""Truthful capacity and venue ownership for ambiguous local order evidence."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal

from tests.operator_diagnostics.test_readiness_completeness import _Directory, _order
from tests.operator_diagnostics.test_readiness_preflight import (
    ScriptedExchange,
    _balance,
    _codes,
    _deployment,
    _seed,
)
from thytrader.exchanges.models import ExchangeOpenOrder
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import DeploymentStatus, OrderKind, OrderStatus
from thytrader.operator.readiness import build_readiness_report
from thytrader.operator.venue_reconciliation import build_venue_reconciliation_report
from thytrader.portfolio.service import PortfolioService


def test_unpriced_ambiguous_entry_cannot_become_zero_exposure_or_free_capacity() -> None:
    """No fabricated price/fee/notional for an unresolved market entry without price evidence."""

    async def scenario() -> None:
        store = InMemoryExecutionStore()
        book = await _seed(store, _deployment())
        order = await _order(store, book, "BTC-USDC", "10", status=OrderStatus.UNKNOWN)
        await store.save_order(replace(order, price=None, kind=OrderKind.MARKETABLE))
        service = PortfolioService(ScriptedExchange((_balance("USDC", "100"),)))
        report = await build_readiness_report(
            portfolio=service,
            execution=store,
            risk_policies=None,
            portfolios=_Directory(()),
        )
        account = report.payload.account
        assert account is not None and account.current_exposure is None
        assert account.remaining_entry_capacity is None and account.capital_base is None
        assert account.inventory.unpriced_entry_order_ids == (order.id,)
        assert report.payload.deployments[0].exposure is None
        venue = await build_venue_reconciliation_report(portfolio=service, execution=store)
        assert venue.payload.quote_currencies[0].managed_working_buy_notional is None

    asyncio.run(scenario())


def test_stopped_terminal_local_order_at_venue_is_managed_drift_not_foreign() -> None:
    """Historical local IDs retain ownership even when a stopped flat book disagrees on status."""

    async def scenario() -> None:
        store = InMemoryExecutionStore()
        book = await _seed(store, _deployment(status=DeploymentStatus.STOPPED))
        order = await _order(
            store, book, "BTC-USDC", "10", venue_id="terminal-local", status=OrderStatus.CANCELED
        )
        service = PortfolioService(
            ScriptedExchange(
                orders=(
                    ExchangeOpenOrder(
                        "terminal-local",
                        "BTC-USDC",
                        "buy",
                        "CANCEL_QUEUED",
                        order.client_order_id,
                    ),
                )
            )
        )
        report = await build_venue_reconciliation_report(portfolio=service, execution=store)
        assert report.payload.orders.foreign == ()
        assert "EXTERNAL_OPEN_ORDERS" not in _codes(report)
        assert "MANAGED_ORDER_STATUS_MISMATCH" in _codes(report)
        assert report.overall_status.value == "degraded"
        assert (await store.get_deployment(book.id)).deployment.status is DeploymentStatus.STOPPED
        assert order.price == Decimal("10")

    asyncio.run(scenario())
