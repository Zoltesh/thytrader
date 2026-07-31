"""Tests for the read-only published signal-evaluation command."""

from __future__ import annotations

import pytest

from thytrader.research.cli import main


def test_cli_help_describes_read_only_published_run_evaluation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can discover the command without database or secret configuration."""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])

    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "published research run" in output
    assert "run_fingerprint" in output
    assert "--pretty" in output
