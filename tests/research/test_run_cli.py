"""Tests for immutable executable backtest-run publication arguments."""

from __future__ import annotations

from thytrader.research.run_cli import _parser, backtest_execution_fingerprint


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


def _publication_arguments() -> list[str]:
    """Return one complete canonical command argument vector."""
    return [
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


def test_backtest_execution_identity_ignores_request_id_and_publication_time() -> None:
    """Identical executable assumptions must have one stable operator-facing identity."""
    first = _parser().parse_args(_publication_arguments())
    second = _parser().parse_args(_publication_arguments())

    assert backtest_execution_fingerprint(first) == backtest_execution_fingerprint(second)


def test_backtest_execution_identity_normalizes_equivalent_decimal_inputs() -> None:
    """CLI syntax must not create a distinct executable identity for equal Decimal assumptions."""
    canonical = _parser().parse_args(_publication_arguments())
    equivalent = _publication_arguments()
    equivalent[equivalent.index("10000")] = "10000.0"
    equivalent[equivalent.index("0.001")] = "0.0010"
    equivalent[equivalent.index("0.002")] = "0.0020"
    equivalent[equivalent.index("1")] = "1.0"

    assert backtest_execution_fingerprint(_parser().parse_args(equivalent)) == (
        backtest_execution_fingerprint(canonical)
    )
