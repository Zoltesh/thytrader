"""HTTP contract for the versioned risk-policy registry."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.persistence.audit_events import InMemoryAuditEventStore
from thytrader.risk.models import compiled_default_active_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.authoring import DisabledStrategyDraftStore
from thytrader.strategies.publication import DisabledStrategyPublicationStore


def test_get_risk_policy_returns_compiled_default_without_postgres() -> None:
    """Observation stays available when durable publication storage is absent."""
    app = create_app(Settings(_env_file=None))
    expected = compiled_default_active_policy()
    with TestClient(app) as client:
        response = client.get("/api/v1/risk-policy")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "compiled_default"
    assert body["policy_fingerprint"] == expected.policy_fingerprint
    assert body["paper_capital_quote"] == "100000"
    assert body["daily_loss_limit_fraction"] == "1"
    assert body["max_entry_orders_per_minute"] == 60
    assert body["reference_price_collar_fraction"] == "0.5"
    assert body["allow_intra_strategy_pyramiding"] is False
    assert body["max_concurrent_running_deployments"] == 8


def test_put_risk_policy_requires_durable_storage() -> None:
    """Publication without PostgreSQL must fail closed, not invent a local policy."""
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        response = client.put(
            "/api/v1/risk-policy",
            json={
                "max_concurrent_running_deployments": 2,
                "max_concurrent_open_positions": 2,
                "max_portfolio_exposure_fraction": "1",
                "per_product_max_exposure_fraction": "1",
                "paper_capital_quote": "20000",
            },
        )
    assert response.status_code == 503


def test_put_risk_policy_publishes_an_immutable_version() -> None:
    """A durable in-memory store accepts PUT and then serves the published fingerprint."""
    store = InMemoryRiskPolicyStore()
    audit = InMemoryAuditEventStore()
    app = create_app(
        Settings(_env_file=None),
        risk_policy_store=store,
        audit_event_store=audit,
        strategy_draft_store=DisabledStrategyDraftStore(),
        strategy_store=DisabledStrategyPublicationStore(),
    )
    payload = {
        "product_allowlist": ["BTC-USD", "ETH-USD"],
        "max_concurrent_running_deployments": 3,
        "max_concurrent_open_positions": 3,
        "max_portfolio_exposure_fraction": "1",
        "per_product_max_exposure_fraction": "1",
        "paper_capital_quote": "40000",
        "allocations": [],
    }
    with TestClient(app) as client:
        created = client.put("/api/v1/risk-policy", json=payload)
        fetched = client.get("/api/v1/risk-policy")
    assert created.status_code == 200
    body = created.json()
    assert body["source"] == "published"
    assert body["version"] == 1
    assert body["product_allowlist"] == ["BTC-USD", "ETH-USD"]
    assert body["quote_currency"] == "USDC"
    assert fetched.json()["policy_fingerprint"] == body["policy_fingerprint"]
    events = asyncio.run(audit.list_recent(limit=20))
    assert any(event.action == "set_risk_policy" for event in events)
