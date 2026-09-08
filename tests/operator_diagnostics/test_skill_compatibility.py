"""Compatibility checks between shipped skills and the operator contract."""

from pathlib import Path

from thytrader.operator.models import OPERATOR_API_PREFIX, SCHEMA_VERSION

_ROOT = Path(__file__).parents[2]
_OPERATOR_SKILL = _ROOT / "skills" / "thytrader-operator" / "SKILL.md"
_DIAGNOSTICS = _ROOT / "skills" / "thytrader-operator" / "references" / "diagnostics-api.md"
_SCHEMAS = _ROOT / "skills" / "thytrader-operator" / "references" / "report-schemas.md"
_RESEARCH_SKILL = _ROOT / "skills" / "thytrader-research" / "SKILL.md"


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
