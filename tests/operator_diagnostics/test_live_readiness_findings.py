"""Operator reports name the live filled-without-fills pause and disclose demo candles."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID

from pydantic import SecretStr

from thytrader.config import Settings
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    RuntimePhase,
)
from thytrader.execution.reconcile import FILLED_WITHOUT_REST_FILLS_DETAIL
from thytrader.market_data.worker_state import DisabledMarketDataWorkerStateStore
from thytrader.operator.service import OperatorDiagnostics
from thytrader.persistence.audit_events import InMemoryAuditEventStore
from thytrader.persistence.backtest_results import DisabledBacktestResultStore
from thytrader.persistence.portfolio_history import InMemoryPortfolioHistoryStore
from thytrader.portfolio.demo import DemoExchangeAccount
from thytrader.portfolio.service import PortfolioService
from thytrader.strategies.authoring import DisabledStrategyDraftStore
from thytrader.strategies.publication import DisabledStrategyPublicationStore


def _deployment(*, mode: DeploymentMode, mismatch: str | None = None) -> Deployment:
    """Return one deployment row with an optional pause mismatch."""
    now = utc_now()
    return Deployment(
        id=uuid7(now),
        strategy_fingerprint="sha256:" + "c" * 64,
        strategy_id=UUID(int=7),
        product_id="BTC-USDC",
        mode=mode,
        status=DeploymentStatus.PAUSED if mismatch else DeploymentStatus.RUNNING,
        cash=Decimal("0"),
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
        mismatch_detail=mismatch,
    )


def _diagnostics(execution: InMemoryExecutionStore, settings: Settings) -> OperatorDiagnostics:
    """Build diagnostics over one seeded execution store."""
    return OperatorDiagnostics(
        settings=settings,
        portfolio=PortfolioService(DemoExchangeAccount(), demo=True),
        market_data_state=DisabledMarketDataWorkerStateStore(),
        history=InMemoryPortfolioHistoryStore(),
        publications=DisabledStrategyPublicationStore(),
        drafts=DisabledStrategyDraftStore(),
        backtests=DisabledBacktestResultStore(),
        execution=execution,
        audit=InMemoryAuditEventStore(),
    )


def test_live_filled_without_rest_fills_emits_precise_code() -> None:
    """The live pause keeps STATE_MISMATCH and adds FILLED_WITHOUT_FILL once."""
    execution = InMemoryExecutionStore()
    live = _deployment(mode=DeploymentMode.LIVE, mismatch=FILLED_WITHOUT_REST_FILLS_DETAIL)
    asyncio.run(execution.create_deployment(live))
    report = asyncio.run(_diagnostics(execution, Settings(_env_file=None)).reconciliation())
    codes = [
        finding.reason_code
        for finding in report.payload.findings
        if finding.deployment_id == live.id
    ]
    assert "STATE_MISMATCH" in codes
    assert codes.count("FILLED_WITHOUT_FILL") == 1


def test_demo_market_data_is_disclosed_for_active_paper_without_credentials() -> None:
    """Paper books on synthetic demo candles are named, not silent."""
    execution = InMemoryExecutionStore()
    asyncio.run(execution.create_deployment(_deployment(mode=DeploymentMode.PAPER)))
    diagnostics = _diagnostics(execution, Settings(_env_file=None))
    components = asyncio.run(diagnostics._execution_market_data_components())
    assert [component.reason_code for component in components] == ["DEMO_MARKET_DATA"]
    assert "synthetic demo candles" in components[0].detail


def test_no_demo_disclosure_when_credentials_are_configured() -> None:
    """With Coinbase credentials the execution worker uses venue candles."""
    execution = InMemoryExecutionStore()
    asyncio.run(execution.create_deployment(_deployment(mode=DeploymentMode.PAPER)))
    settings = Settings(
        _env_file=None,
        coinbase_api_key_name=SecretStr("organizations/x/apiKeys/y"),
        coinbase_api_private_key=SecretStr(
            "-----BEGIN EC PRIVATE KEY-----\nabc\n-----END EC PRIVATE KEY-----"
        ),
    )
    components = asyncio.run(_diagnostics(execution, settings)._execution_market_data_components())
    assert components == ()
