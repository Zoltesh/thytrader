"""CLI tests for confirmation-gated research mutations."""

from __future__ import annotations

import pytest

from thytrader.research.mutation_cli import main


def test_research_help_mentions_confirm_and_no_trading(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can discover the confirmation gate without a database."""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "--confirm" in output
    assert "no paper or live" in output.lower() or "no paper" in output.lower()


def test_create_draft_without_confirm_does_not_write() -> None:
    """Omitting --confirm must exit before any research mutation."""
    with pytest.raises(SystemExit) as raised:
        main(["create-draft"])
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
