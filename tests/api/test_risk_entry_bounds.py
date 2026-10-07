"""Strict HTTP publication of optional risk entry bounds without changing legacy defaults."""

from fastapi.testclient import TestClient
import pytest

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.risk.models import RiskPolicyWrite, compiled_default_active_policy
from thytrader.risk.store import InMemoryRiskPolicyStore


def test_optional_bounds_round_trip_through_the_versioned_policy_surface() -> None:
    """New optional fields publish canonically, while an omitted field remains unset."""
    store = InMemoryRiskPolicyStore()
    app = create_app(Settings(_env_file=None), risk_policy_store=store)
    payload = RiskPolicyWrite.model_validate(
        {
            **compiled_default_active_policy().definition.model_dump(
                mode="json", exclude={"policy_id", "version", "schema_version"}
            ),
            "max_order_quantity": "0.5",
            "max_order_notional_quote": "100",
            "min_available_quote_reserve": "50",
        }
    ).model_dump(mode="json")
    with TestClient(app) as client:
        default = client.get("/api/v1/risk-policy").json()
        assert default["max_order_quantity"] is None
        published = client.put("/api/v1/risk-policy", json=payload)
        assert published.status_code == 200
        body = published.json()
        fetched = client.get("/api/v1/risk-policy").json()
        assert fetched["policy_fingerprint"] == body["policy_fingerprint"]
        assert fetched["max_order_quantity"] == "0.5"
        assert fetched["max_order_notional_quote"] == "100"
        assert fetched["min_available_quote_reserve"] == "50"


@pytest.mark.parametrize(
    "field", ["max_order_quantity", "max_order_notional_quote", "min_available_quote_reserve"]
)
@pytest.mark.parametrize("invalid", [0.5, "0", "-1", "NaN", "Infinity"])
def test_optional_bounds_reject_floats_nonpositive_and_nonfinite_values(
    field: str, invalid: str | float
) -> None:
    """External monetary/quantity input must be a positive finite decimal string."""
    app = create_app(Settings(_env_file=None), risk_policy_store=InMemoryRiskPolicyStore())
    payload = compiled_default_active_policy().definition.model_dump(
        mode="json", exclude={"policy_id", "version", "schema_version"}
    )
    payload[field] = invalid
    with TestClient(app) as client:
        assert client.put("/api/v1/risk-policy", json=payload).status_code == 422
