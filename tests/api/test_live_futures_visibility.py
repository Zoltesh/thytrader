"""Read-only live futures visibility through the real HTTP and CLI contracts."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.api.test_paper_futures_runtime_lane import _Catalog
from tests.operator_diagnostics.test_futures_books_report import _binding, _deployment
from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.execution.futures_start import FuturesStart
from thytrader.operator.futures_books_report import FuturesBooksReport
from thytrader.operator.http import fetch_operator_report
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, DeploymentStatus, RuntimePhase


def test_injected_live_book_is_visible_without_claiming_order_authority() -> None:
    """HTTP lists deployed live states even though live starts remain refused."""
    execution = InMemoryExecutionStore()
    deployment = replace(
        _deployment(cash="0"),
        mode=DeploymentMode.LIVE,
        phase=RuntimePhase.FLAT,
        paper_starting_cash=None,
        allocated_capital=Decimal(100),
    )
    asyncio.run(execution.create_deployment(deployment))
    contracts = InMemoryFuturesContractStore()
    asyncio.run(contracts.bind_contract(_binding(deployment.id)))
    app = create_app(Settings(_env_file=None), execution_store=execution)
    app.state.futures_start = FuturesStart(contracts=contracts, observations=_Catalog())
    with TestClient(app) as client:
        response = client.get(f"/api/v1/deployments/{deployment.id}/futures")
        assert response.status_code == 200
        book = response.json()
        assert book["mode"] == "live"
        assert book["orderable"] is False
        assert book["live"]["match_state"] == "unknown"
        assert book["live"]["funding_drift_reason"] == "SETTLEMENT_PROVENANCE_UNAVAILABLE"
        assert book["live"]["venue_contracts"] is None
        assert book["equity"] == "100"
        assert "venue_account" in book["unknown"]
        assert book["entry_blocks"]
        report_response = client.get("/api/v1/operator/futures-books")
        assert report_response.status_code == 200
        report = FuturesBooksReport.model_validate(report_response.json())
        assert len(report.payload.books) == 1
        assert report.payload.books[0].deployment_id == deployment.id
        assert report.payload.books[0].model_dump()["live"]["match_state"] == "unknown"
        assert report.payload.live_supported is False
        with patch("thytrader.operator.http.request_json", return_value=report_response.json()):
            fetched = fetch_operator_report(
                base_url="http://127.0.0.1:18080", command="futures-books"
            )
        assert isinstance(fetched, FuturesBooksReport)
        assert fetched.payload.books[0].live is not None
        assert fetched.payload.books[0].live.funding_drift_status == "unknown"
        stopped = replace(deployment, status=DeploymentStatus.STOPPED, futures_derisk_pending=True)
        asyncio.run(execution.save_deployment(stopped))
        stopped_report = client.get("/api/v1/operator/futures-books").json()
        assert stopped_report["overall_status"] != "healthy"
        asyncio.run(execution.save_deployment(deployment))
        fleet = client.get("/api/v1/operator/fleet-health").json()
        scope = next(
            row for row in fleet["payload"]["entries"]["scopes"] if row["scope"] == "CFM-USD"
        )
        assert "FUTURES_LIVE_DISABLED" in scope["reason_codes"]
