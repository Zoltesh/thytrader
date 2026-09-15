"""Validation and fingerprint stability for risk-policy documents."""

from uuid import UUID

from pydantic import ValidationError
import pytest

from thytrader.risk.models import (
    COMPILED_POLICY_ID,
    CapitalAllocation,
    RiskPolicyDefinition,
    RiskPolicySource,
    compiled_default_active_policy,
    compiled_default_risk_policy,
    risk_policy_fingerprint,
)


def test_compiled_default_is_the_conservative_multi_asset_envelope() -> None:
    """The compiled default must permit eight slots per mode and a 100000 paper book."""
    definition = compiled_default_risk_policy()
    assert definition.schema_version == "thytrader-risk-policy-v1"
    assert definition.policy_id == COMPILED_POLICY_ID
    assert definition.product_allowlist == ()
    assert definition.allocations == ()
    assert definition.max_concurrent_running_deployments == 8
    assert definition.max_concurrent_open_positions == 8
    assert definition.max_portfolio_exposure_fraction == "1"
    assert definition.per_product_max_exposure_fraction == "1"
    assert definition.paper_capital_quote == "100000"
    active = compiled_default_active_policy()
    assert active.source is RiskPolicySource.COMPILED_DEFAULT
    assert active.policy_fingerprint == risk_policy_fingerprint(definition)
    assert active.policy_fingerprint.startswith("sha256:")


def test_fingerprint_is_stable_under_key_reordering() -> None:
    """Canonical identity must not depend on Python dict insertion order."""
    first = compiled_default_risk_policy()
    second = RiskPolicyDefinition.model_validate(first.model_dump(mode="python"))
    assert risk_policy_fingerprint(first) == risk_policy_fingerprint(second)


def test_allowlist_rejects_duplicates_and_non_usd_spot() -> None:
    """Allowlist entries must be unique BASE-USD product ids."""
    payload = compiled_default_risk_policy().model_dump(mode="python")
    with pytest.raises(ValidationError):
        RiskPolicyDefinition.model_validate(
            {**payload, "product_allowlist": ("BTC-USD", "BTC-USD")}
        )
    with pytest.raises(ValidationError):
        RiskPolicyDefinition.model_validate({**payload, "product_allowlist": ("BTC-USDT",)})


def test_allocations_must_fit_the_paper_book() -> None:
    """Reserved quote cannot exceed paper_capital_quote or repeat a strategy."""
    strategy = UUID("01978a3e-5f2c-7d10-b3a4-0000000000bb")
    other = UUID("01978a3e-5f2c-7d10-b3a4-0000000000cc")
    payload = compiled_default_risk_policy().model_dump(mode="python")
    with pytest.raises(ValidationError):
        RiskPolicyDefinition.model_validate(
            {
                **payload,
                "allocations": (
                    CapitalAllocation(strategy_id=strategy, allocated_quote="60000"),
                    CapitalAllocation(strategy_id=other, allocated_quote="60000"),
                ),
            }
        )
    with pytest.raises(ValidationError):
        RiskPolicyDefinition.model_validate(
            {
                **payload,
                "allocations": (
                    CapitalAllocation(strategy_id=strategy, allocated_quote="1000"),
                    CapitalAllocation(strategy_id=strategy, allocated_quote="2000"),
                ),
            }
        )
