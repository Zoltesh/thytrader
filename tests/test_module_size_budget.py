"""Module size budget: new god files fail CI, existing oversize files may only shrink.

A module over its budget almost always mixes concerns (see AGENTS.md "Keep modules
cohesive"). Files over budget today are listed in ``module_size_allowlist.json`` with a
ceiling equal to their size when the budget was introduced. A ceiling never goes up:
split the module instead. When an allowlisted file falls to or under its budget, remove
its entry so it cannot grow back.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = Path(__file__).with_name("module_size_allowlist.json")

#: Hard line budgets. Aim well below them: about 600 for Python and 400 for TS/Svelte.
PYTHON_BUDGET = 800
WEB_BUDGET = 600

_WEB_TEST_SUFFIXES = (".spec.ts", ".test.ts", ".e2e.ts")


def _line_count(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _budgeted_files() -> dict[str, int]:
    """Map every budgeted source file (repo-relative POSIX path) to its budget."""
    files: dict[str, int] = {}
    for path in (ROOT / "src" / "thytrader").rglob("*.py"):
        files[path.relative_to(ROOT).as_posix()] = PYTHON_BUDGET
    web = ROOT / "web" / "src"
    for pattern in ("*.ts", "*.svelte"):
        for path in web.rglob(pattern):
            relative = path.relative_to(ROOT).as_posix()
            if relative.endswith(_WEB_TEST_SUFFIXES) or "/e2e/" in relative:
                continue
            files[relative] = WEB_BUDGET
    return files


def _allowlist() -> dict[str, int]:
    loaded = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    return {str(path): int(ceiling) for path, ceiling in loaded.items()}


def test_no_module_exceeds_its_budget_or_its_allowlisted_ceiling() -> None:
    """Over-budget files must be allowlisted and must not grow past their ceiling."""
    allowlist = _allowlist()
    problems: list[str] = []
    for relative, budget in sorted(_budgeted_files().items()):
        lines = _line_count(ROOT / relative)
        if lines <= budget:
            continue
        ceiling = allowlist.get(relative)
        if ceiling is None:
            problems.append(
                f"{relative}: {lines} lines exceeds the {budget}-line budget; split it by "
                "concern (AGENTS.md: Keep modules cohesive)."
            )
        elif lines > ceiling:
            problems.append(
                f"{relative}: grew to {lines} lines past its allowlisted ceiling {ceiling}; "
                "split it instead of raising the ceiling."
            )
    assert not problems, "\n".join(problems)


def test_allowlist_only_names_files_still_over_budget() -> None:
    """Entries for deleted files or files back under budget must be removed (ratchet)."""
    budgets = _budgeted_files()
    stale: list[str] = []
    for relative in sorted(_allowlist()):
        budget = budgets.get(relative)
        if budget is None:
            stale.append(f"{relative}: no longer exists or is not budgeted; remove the entry.")
        elif _line_count(ROOT / relative) <= budget:
            stale.append(f"{relative}: now within budget; remove the entry so it stays there.")
    assert not stale, "\n".join(stale)
