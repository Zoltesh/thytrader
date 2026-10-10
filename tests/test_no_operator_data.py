"""Tracked files never carry one operator's account data (AGENTS.md: Security and privacy).

ThyTrader is open source. Docs, ADRs, skills, comments and fixtures state venue behavior
generically and use synthetic values. This guard fails on tokens that once leaked from a real
account (a name, balances and live record ids). Each token is stored split in two so this file
does not match itself; extend the list when another leak is scrubbed.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]

_LEAKED_TOKENS = tuple(
    head + tail
    for head, tail in (
        ("bray", "den"),
        ("514", ".24"),
        ("514", ".23"),
        ("0.0001", "4174"),
        ("01a0f90a-9834-7d0e-", "afad-ef0b538fee40"),
        ("01a0f93b-9591-7ee1-", "b037-eae7b079c1b6"),
        ("01a0f93b-9591-7ee1-", "b037-eae7b079c1b7"),
    )
)


def _tracked_files() -> list[Path]:
    """Every file Git tracks in this checkout (untracked local data is never scanned)."""
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("not a Git checkout")
    # Fixed argv with the resolved git binary and no shell; nothing here is user input.
    listing = subprocess.run(  # noqa: S603
        [git, "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    )
    return [ROOT / name for name in listing.stdout.decode("utf-8").split("\0") if name]


def test_no_tracked_file_contains_leaked_operator_data() -> None:
    """No tracked text file contains a token known to come from a real account."""
    problems: list[str] = []
    for path in _tracked_files():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8").lower()
        except UnicodeDecodeError:
            continue
        problems.extend(
            f"{path.relative_to(ROOT).as_posix()}: contains a leaked operator token "
            f"({token[:4]}…); use synthetic values"
            for token in _LEAKED_TOKENS
            if token in text
        )
    assert not problems, "\n".join(problems)
