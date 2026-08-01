"""Tests for the deterministic published-run backtest CLI contract."""

from __future__ import annotations

from thytrader.backtest.cli import _parser


def test_cli_help_describes_published_run_simulation_and_persistence() -> None:
    """Operators must be told that this command derives results without trading authority."""
    help_text = _parser().format_help()

    assert "published research run" in help_text
    assert "does not submit orders" in help_text
    assert "run_fingerprint" in help_text
