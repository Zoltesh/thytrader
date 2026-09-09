"""Compatibility checks between shipped skills and the operator contract."""

from __future__ import annotations

import json
from pathlib import Path

from thytrader.operator.models import (
    OPERATOR_API_PREFIX,
    REPORT_KINDS,
    SCHEMA_VERSION,
    OperatorEnvelope,
)

_ROOT = Path(__file__).parents[2]
_OPERATOR_SKILL = _ROOT / "skills" / "thytrader-operator" / "SKILL.md"
_DIAGNOSTICS = _ROOT / "skills" / "thytrader-operator" / "references" / "diagnostics-api.md"
_SCHEMAS = _ROOT / "skills" / "thytrader-operator" / "references" / "report-schemas.md"
_RESEARCH_SKILL = _ROOT / "skills" / "thytrader-research" / "SKILL.md"
_RUNTIME_SKILL = _ROOT / "skills" / "thytrader-runtime" / "SKILL.md"


def test_operator_skill_matches_application_schema_and_routes() -> None:
    """The shipped skill must name the live schema version and HTTP prefix."""
    skill = _OPERATOR_SKILL.read_text(encoding="utf-8")
    diagnostics = _DIAGNOSTICS.read_text(encoding="utf-8")
    schemas = _SCHEMAS.read_text(encoding="utf-8")
    combined = skill + diagnostics + schemas
    assert SCHEMA_VERSION in combined
    assert OPERATOR_API_PREFIX in combined
    for suffix in (
        "/health",
        "/configuration",
        "/exchange",
        "/market-data",
        "/strategies",
        "/performance",
        "/risk",
        "/reconciliation",
        "/runtime",
        "/support-bundle",
    ):
        assert f"{OPERATOR_API_PREFIX}{suffix}" in combined
    assert "thytrader-operator" in skill
    assert "Never places" in skill or "cannot place" in skill.lower() or "Never" in skill


def test_research_skill_requires_confirm_and_forbids_trading() -> None:
    """The research skill must gate mutations and deny paper/live control."""
    skill = _RESEARCH_SKILL.read_text(encoding="utf-8")
    assert "thytrader-research" in skill
    assert "--confirm" in skill
    assert "create-draft" in skill
    assert "submit-backtest" in skill
    assert "Never deploys" in skill or "cannot deploy" in skill
    assert "--local" in skill
    assert "THYTRADER_API_BASE_URL" in skill or "loopback HTTP" in skill.lower()


def test_runtime_skill_requires_confirm_and_live_ack() -> None:
    """Runtime control must be confirmation-gated and distinct from operator/research."""
    skill = _RUNTIME_SKILL.read_text(encoding="utf-8")
    assert "thytrader-runtime" in skill
    assert "--confirm" in skill
    assert "--i-understand-live" in skill
    assert "start" in skill
    assert "pause" in skill
    assert "/api/v1/deployments" in skill
    assert "not an extension" in skill.lower() or "not the operator" in skill.lower()


def test_committed_json_schema_matches_envelope_contract() -> None:
    """The shipped JSON Schema must keep the v1 envelope required keys."""
    schema_path = (
        _ROOT / "skills" / "thytrader-operator" / "references" / "operator-report-v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    required = set(schema["required"])
    envelope_required = set(OperatorEnvelope.model_json_schema()["required"])
    assert envelope_required <= required
    assert "report_kind" in required
    assert "payload" in required
    assert schema["properties"]["schema_version"]["const"] == SCHEMA_VERSION
    assert set(schema["properties"]["report_kind"]["enum"]) == set(REPORT_KINDS)
