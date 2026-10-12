"""Managed live CFM report comparisons never infer ownership from a client-id prefix."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal

from fastapi.testclient import TestClient

from tests.api.test_paper_futures_runtime_lane import _Catalog
from tests.operator_diagnostics.test_futures_books_report import _binding, _deployment
from tests.operator_diagnostics.test_futures_readiness_reconciliation import (
    _Futures,
    _FuturesExchange,
    _snapshot,
    _Store,
)
from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.execution.futures_start import FuturesStart
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.portfolio.service import PortfolioService
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode


def test_http_reconciliation_reports_missing_managed_position_and_readiness_duty() -> None:
    """A complete fresh mirror compares a managed local-flat book, not an external book."""
    execution = InMemoryExecutionStore()
    observation = _snapshot()
    product = observation.positions[0].product_id
    deployment = replace(
        _deployment(cash="0", product_id=product),
        mode=DeploymentMode.LIVE,
        allocated_capital=Decimal(100),
        paper_starting_cash=None,
        futures_derisk_pending=True,
    )
    asyncio.run(execution.create_deployment(deployment))
    contracts = InMemoryFuturesContractStore()
    asyncio.run(contracts.bind_contract(_binding(deployment.id)))
    app = create_app(
        Settings(_env_file=None),
        execution_store=execution,
        portfolio_service=PortfolioService(_FuturesExchange(), demo=False),
        market_data_service=MarketDataService(DemoMarketData(), futures_provider=_Futures()),
    )
    app.state.futures_account_store = _Store(observation)
    app.state.futures_start = FuturesStart(contracts=contracts, observations=_Catalog())
    with TestClient(app) as client:
        response = client.get("/api/v1/operator/venue-reconciliation")
        assert response.status_code == 200
        section = response.json()["payload"]["futures"]
        assert section["positions"][0]["classification"] == "managed_mismatch"
        assert section["positions"][0]["deployment_id"] == str(deployment.id)
        codes = {row["reason_code"] for row in response.json()["payload"]["findings"]}
        assert "FUTURES_MANAGED_POSITION_MISMATCH" in codes
        readiness = client.get("/api/v1/operator/readiness")
        assert readiness.status_code == 200
        codes = {row["reason_code"] for row in readiness.json()["payload"]["findings"]}
        assert "FUTURES_LIVE_BOOKS" in codes
        assert "FUTURES_DERISK_PENDING" in codes
        assert "FUTURES_POSITIONS_EXTERNAL" not in codes
