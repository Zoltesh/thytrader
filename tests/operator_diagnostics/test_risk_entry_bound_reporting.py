"""The primary operator report echoes configured entry bounds and replacement semantics."""

from dataclasses import replace

import pytest

from tests.operator_diagnostics.test_service import _diagnostics
from thytrader.risk.models import RiskPolicyWrite, compiled_default_risk_policy
from thytrader.risk.service import publish_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.runtime_control.cli import _parser, _risk_policy_payload


@pytest.mark.anyio
async def test_primary_risk_report_echoes_optional_bounds_and_full_replacement() -> None:
    """Limits are policy configuration, not a balance; omission removes them only in a successor."""
    store = InMemoryRiskPolicyStore()
    fields = compiled_default_risk_policy().model_dump(
        exclude={"policy_id", "version", "schema_version"}
    )
    write = RiskPolicyWrite.model_validate(
        {
            **fields,
            "max_order_quantity": "0.5",
            "max_order_notional_quote": "100",
            "min_available_quote_reserve": "50",
        }
    )
    prior = await publish_risk_policy(store, write)
    diagnostics = replace(_diagnostics(), risk_policies=store)
    report = await diagnostics.risk()
    assert report.payload.max_order_quantity == "0.5"
    assert report.payload.max_order_notional_quote == "100"
    assert report.payload.min_available_quote_reserve == "50"
    arguments = _parser().parse_args(
        [
            "set-risk-policy",
            "--max-concurrent-running-deployments",
            "8",
            "--max-concurrent-open-positions",
            "8",
            "--max-portfolio-exposure-fraction",
            "1",
            "--per-product-max-exposure-fraction",
            "1",
            "--paper-capital-quote",
            "100000",
            "--confirm",
        ]
    )
    successor = await publish_risk_policy(
        store, RiskPolicyWrite.model_validate(_risk_policy_payload(arguments))
    )
    assert prior.definition.max_order_quantity == "0.5"
    assert successor.definition.max_order_quantity is None
    assert successor.definition.max_order_notional_quote is None
    assert successor.definition.min_available_quote_reserve is None
    report = await diagnostics.risk()
    assert report.payload.max_order_quantity is None
    assert report.payload.max_order_notional_quote is None
    assert report.payload.min_available_quote_reserve is None


def test_cli_help_discloses_omission_unsets_replacement_bounds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Confirmation does not imply retaining omitted bounds; the flag help must say so."""
    with pytest.raises(SystemExit) as exit_info:
        _parser().parse_args(["set-risk-policy", "--help"])
    assert exit_info.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "Publication replaces the whole active policy" in help_text
    assert "omitting this flag unsets the bound" in help_text
    assert "resupply it to retain" in help_text.lower()
