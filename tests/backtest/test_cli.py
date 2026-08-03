"""Tests for the deterministic published-run backtest CLI contract."""

from __future__ import annotations

from thytrader.backtest.cli import _parser


def test_cli_help_describes_published_run_simulation_and_persistence() -> None:
    """Operators must be told that this command derives results without trading authority."""
    help_text = _parser().format_help()

    assert "published research run" in help_text
    assert "does not submit orders" in help_text
    assert "run_fingerprint" in help_text


def test_cli_supports_read_only_result_list_and_show_commands() -> None:
    """Operators can discover immutable results without gaining mutation options."""
    parser = _parser()

    listed = parser.parse_args(["list", "--run-fingerprint", "sha256:" + "a" * 64])
    shown = parser.parse_args(["show", "sha256:" + "b" * 64])

    assert listed.command == "list"
    assert listed.run_fingerprint == "sha256:" + "a" * 64
    assert shown.command == "show"
    assert shown.result_fingerprint == "sha256:" + "b" * 64
