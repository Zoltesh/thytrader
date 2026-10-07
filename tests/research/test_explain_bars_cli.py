"""CLI discovery for read-only execution evidence and bar explanations (ADR 0116)."""

from uuid import uuid4

from thytrader.operator.cli import _execution_quality_text, _parser as operator_parser
from thytrader.research.mutation_cli import _is_mutation, _parser as research_parser


def test_operator_execution_quality_is_a_read_only_command() -> None:
    """The operator parser accepts the report and its twin flag."""
    deployment_id = str(uuid4())
    arguments = operator_parser().parse_args(
        ["execution-quality", "--deployment-id", deployment_id, "--twin", "--format", "text"]
    )
    assert arguments.command == "execution-quality"
    assert arguments.deployment_id == deployment_id
    assert arguments.twin is True
    rendered = _execution_quality_text(
        {"comparable": False, "reasons": ["incomplete_live_evidence"]}, twin=True
    )
    assert "comparable=False" in rendered
    assert "incomplete_live_evidence" in rendered


def test_explain_bars_does_not_require_confirmation() -> None:
    """Bar explanations are a read, so the research mutation gate does not apply."""
    arguments = research_parser().parse_args(
        ["explain-bars", "--result-fingerprint", "sha256:" + "c" * 64, "--limit", "25"]
    )
    assert arguments.command == "explain-bars"
    assert arguments.limit == 25
    assert _is_mutation(arguments) is False
