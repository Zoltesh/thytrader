"""Live futures policy CLI-to-HTTP round trip; publication is not trading authority."""

import pytest

from thytrader.api.routes.risk_policy import RiskPolicyWriteBody, _write_from_body
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.runtime_control.configuration_handlers import _risk_policy_payload
from thytrader.runtime_control.parsers.root import _parser


def test_policy_help_exposes_every_futures_field_and_live_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep every nested policy field discoverable; unset live contract limit is not unlimited."""
    with pytest.raises(SystemExit):
        _parser().parse_args(["set-risk-policy", "--help"])
    help_text = capsys.readouterr().out
    for field in FuturesRiskPolicy.model_fields:
        assert "--futures-" + field.replace("_", "-") in help_text
    assert "live defaults to 1" in " ".join(help_text.split())
    assert "FUTURES_LIVE_UNSUPPORTED" in help_text


def test_live_futures_policy_flags_round_trip() -> None:
    """All live policy fields survive parsing, HTTP validation and domain conversion."""
    arguments = _parser().parse_args(
        [
            "set-risk-policy",
            "--max-concurrent-running-deployments",
            "10",
            "--max-concurrent-open-positions",
            "10",
            "--max-portfolio-exposure-fraction",
            "1",
            "--per-product-max-exposure-fraction",
            "1",
            "--paper-capital-quote",
            "10000",
            "--futures-live-enabled",
            "false",
            "--futures-live-capital-usd",
            "10000",
            "--futures-product-allowlist",
            "BIP-20DEC30-CDE",
            "--futures-live-derisk-margin-ratio",
            "2",
            "--futures-live-funding-drift-tolerance-usd",
            "10",
        ]
    )
    body = RiskPolicyWriteBody.model_validate(_risk_policy_payload(arguments))
    write = _write_from_body(body)
    assert write.futures is not None
    assert write.futures.model_dump(mode="json") == {
        "peg_haircut": "1.25",
        "live_enabled": False,
        "live_capital_usd": "10000",
        "product_allowlist": ["BIP-20DEC30-CDE"],
        "live_derisk_margin_ratio": "2",
        "live_funding_drift_tolerance_usd": "10",
    }
