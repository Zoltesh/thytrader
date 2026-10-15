"""Opt-in start safeguards in the risk policy (ADR 0142)."""

from __future__ import annotations

import json

from thytrader.risk.models import (
    canonical_risk_policy_bytes,
    compiled_default_risk_policy,
    risk_policy_fingerprint,
)
from thytrader.risk.start_safeguards import StartSafeguards
from thytrader.runtime_control.configuration_handlers import _risk_policy_payload
from thytrader.runtime_control.parsers.root import _parser as build_parser

_REQUIRED = (
    "set-risk-policy",
    "--max-concurrent-running-deployments",
    "5",
    "--max-concurrent-open-positions",
    "5",
    "--max-portfolio-exposure-fraction",
    "1",
    "--per-product-max-exposure-fraction",
    "1",
    "--paper-capital-quote",
    "10000",
)


def test_a_policy_without_safeguards_keeps_its_bytes_and_fingerprint() -> None:
    """Unset or all-off safeguards are omitted, so existing policies do not change identity."""
    policy = compiled_default_risk_policy()
    off = policy.model_copy(update={"start_safeguards": StartSafeguards()})
    assert "start_safeguards" not in json.loads(canonical_risk_policy_bytes(policy))
    assert canonical_risk_policy_bytes(off) == canonical_risk_policy_bytes(policy)
    assert risk_policy_fingerprint(off) == risk_policy_fingerprint(policy)
    on = policy.model_copy(
        update={"start_safeguards": StartSafeguards(require_market_history=True)}
    )
    assert json.loads(canonical_risk_policy_bytes(on))["start_safeguards"] == {
        "require_market_history": True
    }
    assert risk_policy_fingerprint(on) != risk_policy_fingerprint(policy)


def test_set_risk_policy_maps_the_safeguard_flags() -> None:
    """Flags turn safeguards on; omitted, the replacement policy carries none."""
    parser = build_parser()
    plain = _risk_policy_payload(parser.parse_args(list(_REQUIRED)))
    assert plain["start_safeguards"] is None
    both = _risk_policy_payload(
        parser.parse_args([*_REQUIRED, "--require-convergence-warmup", "--require-market-history"])
    )
    assert both["start_safeguards"] == {
        "require_convergence_warmup": True,
        "require_market_history": True,
    }
