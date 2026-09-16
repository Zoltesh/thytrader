"""Guard the contributor-only ops-skills completion gate.

These checks lock instruction-file placement: the gate must live in contributor
files and must not be copied into the operator-only `ops/` workspace.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).parents[2]
_GATE_SENTENCE = (
    "A slice is **not done** if ops skills would leave an operator agent unable to "
    "discover or correctly invoke the new surface."
)
_CONTRIBUTOR_GATE_FILES = (
    _ROOT / "AGENTS.md",
    _ROOT / "CLAUDE.md",
    _ROOT / ".cursor" / "rules" / "thytrader-contributor-ops-docs-gate.mdc",
    _ROOT / ".claude" / "skills" / "thytrader-contributor-ops-docs" / "SKILL.md",
)
_OPS_INSTRUCTION_FILES = (
    _ROOT / "ops" / "AGENTS.md",
    _ROOT / "ops" / ".cursor" / "rules" / "thytrader-agent-operations.mdc",
    _ROOT / "ops" / "README.md",
)
_OPS_HARD_STOP_FILES = (
    _ROOT / "ops" / "AGENTS.md",
    _ROOT / "ops" / ".cursor" / "rules" / "thytrader-agent-operations.mdc",
)


def _normalized(text: str) -> str:
    """Collapse whitespace so wrapped markdown still matches the gate sentence."""
    return " ".join(text.split())


def test_contributor_instruction_files_contain_ops_skills_completion_gate() -> None:
    """Contributor AGENTS.md, CLAUDE.md, and Cursor/Claude rules must state the gate."""
    for path in _CONTRIBUTOR_GATE_FILES:
        text = _normalized(path.read_text(encoding="utf-8"))
        assert _GATE_SENTENCE in text, f"missing completion gate in {path.relative_to(_ROOT)}"
        assert "Operating agents must never update documentation or source." in text


def test_ops_workspace_instructions_stay_operator_only() -> None:
    """ops/ instruction files must not assign contributor documentation or source work."""
    forbidden = (
        "completion gate",
        "same change must update",
        "not done if ops skills",
        "update documentation or source",
        "Update documentation",
    )
    for path in _OPS_INSTRUCTION_FILES:
        raw = path.read_text(encoding="utf-8")
        lowered = raw.lower()
        for phrase in forbidden:
            assert phrase.lower() not in lowered, (
                f"ops instruction file {path.relative_to(_ROOT)} must stay operator-only; "
                f"found contributor phrase {phrase!r}"
            )
    for path in _OPS_HARD_STOP_FILES:
        assert "do not edit" in path.read_text(encoding="utf-8").lower()
    ops_agents = (_ROOT / "ops" / "AGENTS.md").read_text(encoding="utf-8")
    assert "not the contributor checkout" in ops_agents
    assert not (
        _ROOT / "ops" / ".cursor" / "rules" / "thytrader-contributor-ops-docs-gate.mdc"
    ).exists()
