"""P2-3 publishes risk evidence without enabling any live futures start path."""

from fastapi.testclient import TestClient
import pytest

from tests.execution.test_htf_filter import _Catalog
from tests.execution.test_paper_futures_books import _strategy
from thytrader.api.app import create_app
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.config import Settings
from thytrader.execution.service import create_deployment
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.library import DisabledStrategyStore
from thytrader.strategies.models import strategy_fingerprint
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, ExecutionConflictError


def test_live_policy_round_trips_http_and_operator_report() -> None:
    """PUT, GET and operator risk share the complete nested model, including explicit false."""
    block = {
        "live_enabled": True,
        "live_capital_usd": "10000",
        "product_allowlist": ["BIP-20DEC30-CDE"],
        "live_derisk_margin_ratio": "2",
        "live_funding_drift_tolerance_usd": "10",
        "peg_haircut": "1.25",
    }
    app = create_app(
        Settings(_env_file=None),
        risk_policy_store=InMemoryRiskPolicyStore(),
        audit_event_store=InMemoryAuditEventStore(),
        strategy_store=DisabledStrategyStore(),
        execution_store=InMemoryExecutionStore(),
    )
    base = {
        "max_concurrent_running_deployments": 10,
        "max_concurrent_open_positions": 10,
        "max_portfolio_exposure_fraction": "1",
        "per_product_max_exposure_fraction": "1",
        "paper_capital_quote": "10000",
    }
    with TestClient(app) as client:
        response = client.put("/api/v1/risk-policy", json={**base, "futures": block})
        assert response.status_code == 200
        assert response.json()["futures"] == block
        fetched = client.get("/api/v1/risk-policy")
        assert fetched.json()["futures"] == block
        report = client.get("/api/v1/operator/risk")
        assert report.status_code == 200
        assert report.json()["payload"]["futures"] == block
        disabled = client.put(
            "/api/v1/risk-policy", json={**base, "futures": {"live_enabled": False}}
        )
        assert disabled.json()["futures"]["live_enabled"] is False
        assert disabled.json()["policy_fingerprint"] != response.json()["policy_fingerprint"]


@pytest.mark.anyio
async def test_service_start_still_refuses_live_futures_without_writes() -> None:
    """The service safety boundary survives independently of HTTP and CLI refusal tests."""
    strategy = _strategy()
    execution = InMemoryExecutionStore()
    with pytest.raises(ExecutionConflictError, match="FUTURES_LIVE_UNSUPPORTED"):
        await create_deployment(
            store=execution,
            publication_store=_Catalog(strategy),
            strategy_fingerprint=strategy_fingerprint(strategy),
            mode=DeploymentMode.LIVE,
            paper_starting_cash=None,
            live_allowed=True,
        )
    assert await execution.list_deployments() == ()
