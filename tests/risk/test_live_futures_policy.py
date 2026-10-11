"""Canonical compatibility and validation of the optional live futures policy fields."""

import pytest

from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.models import compiled_default_risk_policy, risk_policy_fingerprint


def test_unset_live_fields_preserve_main_fingerprint_goldens() -> None:
    """Goldens were executed from pre-P2-3 main for both absent and existing futures blocks."""
    definition = compiled_default_risk_policy()
    assert risk_policy_fingerprint(definition) == (
        "sha256:7e7f02402a5c77932e147a0004cd18ad01a6eed0a8facb0381286e848515e4a6"
    )
    block = FuturesRiskPolicy(
        paper_capital_usd="10000", live_spot_collateral_reserve_quote="1000", max_order_contracts=2
    )
    assert risk_policy_fingerprint(definition.model_copy(update={"futures": block})) == (
        "sha256:eafaeb3c7acdeaf7ebce4dd66458357f01eb9d542cc54ba80a567de7627a524b"
    )
    assert "live_enabled" not in block.model_dump()
    assert block.effective_live_max_order_contracts == 2
    assert FuturesRiskPolicy().effective_live_max_order_contracts == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("live_capital_usd", "NaN"),
        ("live_capital_usd", "Infinity"),
        ("live_capital_usd", "0"),
        ("live_capital_usd", "-1"),
        ("live_derisk_margin_ratio", "1"),
        ("live_derisk_margin_ratio", "101"),
        ("live_funding_drift_tolerance_usd", "0"),
        ("product_allowlist", ["BTC-USDC"]),
        ("product_allowlist", ["BIP-20DEC30-CDE", "BIP-20DEC30-CDE"]),
        ("live_enabled", "yes"),
        ("live_enabled", 1),
    ],
)
def test_live_policy_rejects_invalid_values(field: str, value: object) -> None:
    """Financial strings, explicit boolean opt-in and futures-only unique ids are required."""
    with pytest.raises(ValueError):
        FuturesRiskPolicy.model_validate({field: value})
