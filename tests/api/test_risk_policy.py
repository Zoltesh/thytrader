"""HTTP contract for the versioned risk-policy registry."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.config import Settings
from thytrader.risk.models import compiled_default_active_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.library import DisabledStrategyStore


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
        strategy_store=DisabledStrategyStore(),
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


def test_put_risk_policy_accepts_large_fleet_counts_up_to_the_model_bound() -> None:
    """The HTTP body admits the same 128-slot bound as the policy model, and rejects 129."""
    app = create_app(
        Settings(_env_file=None),
        risk_policy_store=InMemoryRiskPolicyStore(),
        audit_event_store=InMemoryAuditEventStore(),
        strategy_store=DisabledStrategyStore(),
    )
    payload = {
        "max_concurrent_running_deployments": 128,
        "max_concurrent_open_positions": 128,
        "max_portfolio_exposure_fraction": "1",
        "per_product_max_exposure_fraction": "1",
        "paper_capital_quote": "40000",
    }
    with TestClient(app) as client:
        accepted = client.put("/api/v1/risk-policy", json=payload)
        rejected = client.put(
            "/api/v1/risk-policy", json={**payload, "max_concurrent_running_deployments": 129}
        )
    assert accepted.status_code == 200
    assert accepted.json()["max_concurrent_running_deployments"] == 128
    assert rejected.status_code == 422


def test_put_risk_policy_round_trips_the_fleet_clustering_cap() -> None:
    """The HTTP body, response and model share the ADR 0125 fields and bounds."""
    app = create_app(
        Settings(_env_file=None),
        risk_policy_store=InMemoryRiskPolicyStore(),
        audit_event_store=InMemoryAuditEventStore(),
        strategy_store=DisabledStrategyStore(),
    )
    base = {
        "max_concurrent_running_deployments": 8,
        "max_concurrent_open_positions": 8,
        "max_portfolio_exposure_fraction": "1",
        "per_product_max_exposure_fraction": "1",
        "paper_capital_quote": "40000",
    }
    with TestClient(app) as client:
        unset = client.put("/api/v1/risk-policy", json=base)
        widest = client.put(
            "/api/v1/risk-policy",
            json={**base, "max_fleet_entries_per_window": 128, "fleet_entry_window_minutes": 1440},
        )
        clustered = client.put(
            "/api/v1/risk-policy",
            json={**base, "max_fleet_entries_per_window": 4, "fleet_entry_window_minutes": 120},
        )
        fetched = client.get("/api/v1/risk-policy")
        too_many = client.put(
            "/api/v1/risk-policy",
            json={**base, "max_fleet_entries_per_window": 129, "fleet_entry_window_minutes": 120},
        )
        too_long = client.put(
            "/api/v1/risk-policy",
            json={**base, "max_fleet_entries_per_window": 4, "fleet_entry_window_minutes": 1441},
        )
        unpaired = client.put(
            "/api/v1/risk-policy", json={**base, "max_fleet_entries_per_window": 4}
        )
    assert unset.status_code == 200
    assert unset.json()["max_fleet_entries_per_window"] is None
    assert unset.json()["fleet_entry_window_minutes"] is None
    assert widest.status_code == 200
    assert clustered.status_code == 200
    assert fetched.json()["max_fleet_entries_per_window"] == 4
    assert fetched.json()["fleet_entry_window_minutes"] == 120
    assert fetched.json()["policy_fingerprint"] == clustered.json()["policy_fingerprint"]
    assert too_many.status_code == 422
    assert too_long.status_code == 422
    assert unpaired.status_code == 422
    assert "must be set together" in unpaired.text
