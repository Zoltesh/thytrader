"""Repo-wide guard: retired versioned backtest-engine names must not reappear (ADR 0083).

ThyTrader has one unversioned backtest model (``thytrader-backtest``). Operator-facing
surfaces (the web UI source, the canonical skills, and user docs) must not name the
retired per-version engines, label anything V1-V4, or bring back the engine-support
matrix. Naming the removed ``engine_contract_version`` field to say it is rejected is
allowed (operator agents need that migration hint). Historical ADRs, plans, and
migrations are intentionally out of scope.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

_ROOT = Path(__file__).parents[1]
_SCANNED_ROOTS = (_ROOT / "web" / "src", _ROOT / "skills", _ROOT / "docs" / "user")
_SCANNED_SUFFIXES = frozenset({".ts", ".js", ".svelte", ".md", ".json", ".css", ".html", ".txt"})
_FORBIDDEN = (
    (re.compile(r"thytrader-bar-(?:backtest-|signal-)?v\d", re.IGNORECASE), "retired engine id"),
    (re.compile(r"\bV[1-4]\b"), "V1-V4 engine label"),
    (re.compile(r"engine[_-]?support", re.IGNORECASE), "engine-support matrix"),
)


def _scanned_files() -> tuple[Path, ...]:
    """Return every text file under the scanned operator-facing roots."""
    return tuple(
        sorted(
            path
            for root in _SCANNED_ROOTS
            if root.exists()
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix in _SCANNED_SUFFIXES
            and "node_modules" not in path.parts
        )
    )


def test_scan_covers_the_operator_facing_roots() -> None:
    """The guard is only meaningful if it actually reads the UI, skills, and user docs."""
    files = _scanned_files()
    for root in _SCANNED_ROOTS:
        assert any(root in path.parents for path in files), root


@pytest.mark.parametrize(("pattern", "label"), _FORBIDDEN)
def test_operator_facing_text_names_no_versioned_backtest_engine(
    pattern: re.Pattern[str], label: str
) -> None:
    """No UI source, skill, or user doc may reintroduce a versioned engine or selector."""
    offenders = [
        f"{path.relative_to(_ROOT)}:{number}: {line.strip()}"
        for path in _scanned_files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if pattern.search(line)
    ]
    assert offenders == [], f"{label} found:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    "sample",
    [
        "thytrader-bar-backtest-v4",
        "engine: V2 spread",
        "thytrader-bar-signal-v1",
        "GET /api/v1/research/engine-support",
    ],
)
def test_guard_patterns_catch_known_regressions(sample: str) -> None:
    """Each known regression spelling trips at least one forbidden pattern."""
    assert any(pattern.search(sample) for pattern, _label in _FORBIDDEN)
