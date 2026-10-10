"""The primary operator risk report echoes the correlation-aware limits (ADR 0125)."""

from dataclasses import replace

import pytest

from tests.operator_diagnostics.test_service import _diagnostics
from thytrader.operator.runtime_models import RiskReport
from thytrader.risk.models import RiskPolicyWrite, compiled_default_risk_policy
from thytrader.risk.service import publish_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore


@pytest.mark.anyio
async def test_primary_risk_report_echoes_the_clustering_cap_up_to_its_bounds() -> None:
    """The payload is null when unset and carries the model's widest configured values."""
    store = InMemoryRiskPolicyStore()
    diagnostics = replace(_diagnostics(), risk_policies=store)
    unset = await diagnostics.risk()
    assert unset.payload.max_fleet_entries_per_window is None
    assert unset.payload.fleet_entry_window_minutes is None
    fields = compiled_default_risk_policy().model_dump(
        exclude={"policy_id", "version", "schema_version"}
    )
    await publish_risk_policy(
        store,
        RiskPolicyWrite.model_validate(
            {**fields, "max_fleet_entries_per_window": 128, "fleet_entry_window_minutes": 1440}
        ),
    )

    report = await diagnostics.risk()

    assert report.payload.max_fleet_entries_per_window == 128
    assert report.payload.fleet_entry_window_minutes == 1440
    assert RiskReport.model_validate(report.model_dump(mode="json")) == report


@pytest.mark.anyio
async def test_primary_risk_report_echoes_the_beta_cap() -> None:
    """The β cap fields are configuration echoes: null when unset, the set values otherwise."""
    store = InMemoryRiskPolicyStore()
    diagnostics = replace(_diagnostics(), risk_policies=store)
    unset = await diagnostics.risk()
    assert unset.payload.max_btc_beta_exposure_fraction is None
    assert unset.payload.max_btc_beta_exposure_quote is None
    fields = compiled_default_risk_policy().model_dump(
        exclude={"policy_id", "version", "schema_version"}
    )
    await publish_risk_policy(
        store,
        RiskPolicyWrite.model_validate(
            {
                **fields,
                "max_btc_beta_exposure_fraction": "0.6",
                "max_btc_beta_exposure_quote": "300",
            }
        ),
    )

    report = await diagnostics.risk()

    assert report.payload.max_btc_beta_exposure_fraction == "0.6"
    assert report.payload.max_btc_beta_exposure_quote == "300"
    assert RiskReport.model_validate(report.model_dump(mode="json")) == report
