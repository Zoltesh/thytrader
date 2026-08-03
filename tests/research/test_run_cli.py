"""Tests for immutable executable backtest-run publication arguments."""

from __future__ import annotations

from thytrader.research.run_cli import _parser


def test_publish_backtest_parser_requires_explicit_identity_assumptions() -> None:
    """Backtest publication binds all execution-relevant assumptions into the run request."""
    arguments = _parser().parse_args(
        [
            "publish-backtest",
            "--strategy-fingerprint",
            "sha256:" + "a" * 64,
            "--dataset-fingerprint",
            "sha256:" + "b" * 64,
            "--evaluation-start",
            "2026-08-01T00:00:00Z",
            "--evaluation-end",
            "2026-08-02T00:00:00Z",
            "--initial-quote-balance",
            "10000",
            "--maker-fee-rate",
            "0.001",
            "--taker-fee-rate",
            "0.002",
            "--fixed-slippage-bps",
            "1",
        ]
    )

    assert arguments.command == "publish-backtest"
    assert arguments.initial_quote_balance == "10000"
