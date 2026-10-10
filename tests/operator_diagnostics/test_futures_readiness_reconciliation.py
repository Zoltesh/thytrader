"""Futures in readiness and venue reconciliation (ADR 0127, P0-6)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.exchanges.test_coinbase_cfm import _transport
from tests.operator_diagnostics.test_readiness_preflight import ScriptedExchange, _balance
from thytrader.exchanges.coinbase_cfm import CoinbaseCfmAccount
from thytrader.exchanges.coinbase_futures_catalog import parse_futures_row
from thytrader.exchanges.futures_models import (
    FuturesAccountObservation,
    FuturesAccountStoreUnavailableError,
    FuturesBalanceSummary,
    margin_ratio,
)
from thytrader.exchanges.models import ExchangeOpenOrder
from thytrader.exchanges.read_errors import (
    ExchangeReadError,
    ExchangeReadFailure,
    ExchangeReadFailureKind,
    ExchangeReadOperation,
)
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.operator.readiness import build_readiness_report
from thytrader.operator.readiness_futures import futures_section
from thytrader.operator.venue_reconciliation import build_venue_reconciliation_report
from thytrader.portfolio.service import PortfolioService
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.worker.futures_mirror import observe_futures_account

if TYPE_CHECKING:
    from thytrader.market_data.instruments import FuturesProduct

_FIXTURE = Path(__file__).parents[1] / "exchanges" / "fixtures" / "coinbase_futures_listing.json"


class _Store:
    """Serve one snapshot, nothing, or a storage failure."""

    def __init__(self, latest: FuturesAccountObservation | None, *, fail: bool = False) -> None:
        """Hold the canned answer."""
        self._latest = latest
        self.fail = fail

    async def record(self, observation: FuturesAccountObservation) -> None:
        """Unused: reports are read-only."""
        del observation
        raise AssertionError("read-only")

    async def latest(self) -> FuturesAccountObservation | None:
        """Return the canned snapshot."""
        if self.fail:
            raise FuturesAccountStoreUnavailableError("down")
        return self._latest


class _FuturesExchange(ScriptedExchange):
    """A venue double that also lists futures orders, or fails that listing."""

    def __init__(
        self, futures_orders: tuple[ExchangeOpenOrder, ...] = (), *, fail: bool = False
    ) -> None:
        """Script the futures listing on top of a USDC balance."""
        super().__init__((_balance("USDC", "514.24"),))
        self.futures_orders = futures_orders
        self.fail_futures = fail

    async def list_futures_open_orders(self) -> tuple[ExchangeOpenOrder, ...]:
        """Return the scripted futures orders or fail the listing."""
        if self.fail_futures:
            raise ExchangeReadError(
                ExchangeReadFailure(
                    operation=ExchangeReadOperation.FUTURES_OPEN_ORDERS,
                    kind=ExchangeReadFailureKind.HTTP,
                    http_status=503,
                )
            )
        return self.futures_orders


class _Futures:
    """Futures listing provider double over the verbatim fixture."""

    async def list_futures_products(self) -> tuple[FuturesProduct, ...]:
        """Return the eight FCM rows."""
        payload: dict[str, Any] = json.loads(_FIXTURE.read_text())
        parsed = (parse_futures_row(row) for row in payload["products"])
        return tuple(product for product in parsed if product is not None)


def _snapshot(*, flat: bool = False, now: datetime | None = None, **overrides: Any) -> Any:
    """A snapshot from the documented fixtures, flat or with one ETP short."""
    observation = asyncio.run(
        observe_futures_account(
            CoinbaseCfmAccount(_transport(**overrides)), now or datetime.now(UTC)
        )
    )
    return replace(observation, positions=()) if flat else observation


def _codes(findings: Any) -> set[str]:
    """Reason codes of a findings tuple."""
    return {finding.reason_code for finding in findings}


def test_margin_ratio_is_defined_only_with_a_positive_threshold() -> None:
    """A flat account has no threshold, so no ratio is invented."""
    balance = _snapshot().balance
    assert isinstance(balance, FuturesBalanceSummary)
    assert margin_ratio(balance) == Decimal("11.2304")
    assert margin_ratio(replace(balance, liquidation_threshold=Decimal(0))) is None
    assert margin_ratio(replace(balance, available_margin=None)) is None


def test_readiness_section_discloses_shared_collateral_and_external_positions() -> None:
    """Enabled futures add an info finding; open positions add an advisory one."""
    findings: list[Any] = []
    section = asyncio.run(futures_section(_Store(_snapshot()), findings))
    assert section is not None
    assert section.currency == "USD"
    assert section.futures_buying_power == "412.50"
    assert section.margin_ratio == "11.2304"
    assert "USDC spot balance" in section.collateral_note
    assert _codes(findings) == {"FUTURES_COLLATERAL_SHARED", "FUTURES_POSITIONS_EXTERNAL"}
    flat: list[Any] = []
    asyncio.run(futures_section(_Store(_snapshot(flat=True)), flat))
    assert _codes(flat) == {"FUTURES_COLLATERAL_SHARED"}


def test_readiness_futures_unknowns_are_never_zero() -> None:
    """Stale, failed or unreadable futures evidence is an unknown finding."""
    stale: list[Any] = []
    old = _snapshot(now=datetime.now(UTC) - timedelta(minutes=10))
    asyncio.run(futures_section(_Store(old), stale))
    assert "FUTURES_ACCOUNT_UNKNOWN" in _codes(stale)
    failed: list[Any] = []
    assert asyncio.run(futures_section(_Store(None, fail=True), failed)) is None
    assert _codes(failed) == {"FUTURES_ACCOUNT_UNKNOWN"}
    assert asyncio.run(futures_section(_Store(None), [])) is None
    assert asyncio.run(futures_section(None, [])) is None


def test_readiness_report_carries_the_futures_section() -> None:
    """The fleet preflight includes the section and stays non-degraded on info alone."""
    flat = _snapshot(flat=True)

    async def exercise() -> None:
        """Build the fleet report with a flat enabled account."""
        report = await build_readiness_report(
            portfolio=PortfolioService(_FuturesExchange(), demo=False),
            execution=InMemoryExecutionStore(),
            risk_policies=None,
            portfolios=None,
            futures_account=_Store(flat),
        )
        assert report.payload.futures is not None
        assert report.payload.futures.positions == ()
        assert "FUTURES_COLLATERAL_SHARED" in _codes(report.payload.findings)

    asyncio.run(exercise())


def _reconcile(exchange: Any, store: Any, *, catalog: bool = True) -> Any:
    """Build the venue report with an empty managed fleet."""
    market = MarketDataService(DemoMarketData(), futures_provider=_Futures() if catalog else None)
    return asyncio.run(
        build_venue_reconciliation_report(
            portfolio=PortfolioService(exchange, demo=False),
            execution=InMemoryExecutionStore(),
            futures_account=store,
            market_data=market,
        )
    )


def test_external_positions_and_orders_are_unmanaged_exposure_in_usd() -> None:
    """One ETP short at 2480.5 with contract size 0.1 is 248.05 USD of unmanaged notional."""
    order = ExchangeOpenOrder(
        venue_order_id="v-1", product_id="BIP-20DEC30-CDE", side="buy", status="OPEN"
    )
    report = _reconcile(_FuturesExchange((order,)), _Store(_snapshot()))
    futures = report.payload.futures
    assert futures is not None
    assert futures.positions_source == "mirror_snapshot"
    (row,) = futures.positions
    assert (row.underlying, row.contract_size, row.notional_usd) == ("ETH", "0.1", "248.05")
    assert row.classification == "external_unmanaged"
    assert futures.unmanaged_notional_usd == "248.05"
    assert futures.orders is not None
    assert futures.orders[0].product_id == "BIP-20DEC30-CDE"
    assert futures.orders_listing.scope == "futures_order_history_nonterminal"
    codes = _codes(report.payload.findings)
    assert {"FUTURES_EXTERNAL_POSITIONS", "FUTURES_EXTERNAL_ORDERS"} <= codes
    assert report.overall_status.value == "healthy"


def test_unknown_futures_evidence_degrades_and_never_reads_as_none() -> None:
    """A failed order listing or stale snapshot is unknown; unknown size means no total."""
    failing = _reconcile(_FuturesExchange(fail=True), _Store(_snapshot()))
    assert failing.payload.futures.orders is None
    assert "FUTURES_ORDERS_LISTING_INCOMPLETE" in _codes(failing.payload.findings)
    assert failing.overall_status.value == "degraded"
    old = _snapshot(now=datetime.now(UTC) - timedelta(minutes=10))
    stale = _reconcile(_FuturesExchange(), _Store(old), catalog=False)
    assert stale.payload.futures.positions_source == "stale"
    assert stale.payload.futures.unmanaged_notional_usd is None
    assert "FUTURES_POSITIONS_UNKNOWN" in _codes(stale.payload.findings)


def test_without_a_mirror_or_futures_capability_nothing_is_claimed() -> None:
    """No snapshot and an adapter without futures listing are 'not observed', not failures."""
    report = _reconcile(ScriptedExchange((_balance("USDC", "1"),)), None)
    futures = report.payload.futures
    assert futures is not None
    assert futures.positions_source == "not_observed"
    assert futures.positions is None
    assert futures.orders is None
    assert not {c for c in _codes(report.payload.findings) if c.startswith("FUTURES_")}
