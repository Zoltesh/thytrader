"""Tests for immutable executable backtest-run publication arguments."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from thytrader.research.run_cli import _parser, backtest_execution_fingerprint, main

if TYPE_CHECKING:
    import argparse


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

    assert backtest_execution_fingerprint(first, quote_currency="USD") == (
        backtest_execution_fingerprint(second, quote_currency="USD")
    )


def test_backtest_execution_identity_normalizes_equivalent_decimal_inputs() -> None:
    """CLI syntax must not create a distinct executable identity for equal Decimal assumptions."""
    canonical = _parser().parse_args(_publication_arguments())
    equivalent = _publication_arguments()
    equivalent[equivalent.index("10000")] = "10000.0"
    equivalent[equivalent.index("0.001")] = "0.0010"
    equivalent[equivalent.index("0.002")] = "0.0020"
    equivalent[equivalent.index("1")] = "1.0"

    assert backtest_execution_fingerprint(
        _parser().parse_args(equivalent), quote_currency="USD"
    ) == backtest_execution_fingerprint(canonical, quote_currency="USD")


def test_backtest_execution_identity_hashes_optional_spread_stress() -> None:
    """Spread stress is an explicit immutable input; omitted means the unstressed default."""
    base = _publication_arguments()
    omitted = _parser().parse_args(base)
    zero = _parser().parse_args([*base, "--spread-bps", "0"])
    with_spread = _parser().parse_args([*base, "--spread-bps", "10.00"])
    equivalent = _parser().parse_args([*base, "--spread-bps", "10"])
    different = _parser().parse_args([*base, "--spread-bps", "25"])

    def identity(arguments: argparse.Namespace) -> str:
        return backtest_execution_fingerprint(arguments, quote_currency="USD")

    assert identity(omitted) == identity(zero)
    assert identity(with_spread) == identity(equivalent)
    assert len({identity(omitted), identity(with_spread), identity(different)}) == 3


def test_publish_rejects_the_removed_engine_contract_flag() -> None:
    """The retired engine selector fails with an explicit migration message, not argparse noise."""
    for flag in (
        ["--engine-contract-version", "thytrader-backtest"],
        ["--engine-contract-version=thytrader-backtest"],
    ):
        with pytest.raises(SystemExit, match="--engine-contract-version was removed"):
            main([*_publication_arguments(), *flag])


def test_publish_help_describes_the_single_model(capsys: pytest.CaptureFixture[str]) -> None:
    """--help documents spread stress and names no engine selector."""
    for argv in (["--help"], ["publish-backtest", "--help"]):
        with pytest.raises(SystemExit):
            _parser().parse_args(argv)
    help_text = " ".join(capsys.readouterr().out.split())
    assert "--spread-bps" in help_text
    assert "--engine-contract-version" not in help_text
    assert "no engine selector" in help_text


def test_backtest_execution_identity_includes_indicator_datasets() -> None:
    """Extra-TF dataset bindings are execution-significant and ordered by duration."""
    baseline = _parser().parse_args(_publication_arguments())
    hour = "sha256:" + "c" * 64
    day = "sha256:" + "d" * 64
    with_hour = _parser().parse_args(
        [*_publication_arguments(), "--indicator-dataset-fingerprint", f"1h={hour}"]
    )
    with_hour_then_day = _parser().parse_args(
        [
            *_publication_arguments(),
            "--indicator-dataset-fingerprint",
            f"1h={hour}",
            "--indicator-dataset-fingerprint",
            f"1d={day}",
        ]
    )
    with_day_then_hour = _parser().parse_args(
        [
            *_publication_arguments(),
            "--indicator-dataset-fingerprint",
            f"1d={day}",
            "--indicator-dataset-fingerprint",
            f"1h={hour}",
        ]
    )
    assert backtest_execution_fingerprint(baseline, quote_currency="USD") != (
        backtest_execution_fingerprint(with_hour, quote_currency="USD")
    )
    assert backtest_execution_fingerprint(with_hour_then_day, quote_currency="USD") == (
        backtest_execution_fingerprint(with_day_then_hour, quote_currency="USD")
    )


def test_backtest_execution_identity_distinguishes_quote_currency() -> None:
    """USDC and USD capital assumptions must not share one execution fingerprint."""
    arguments = _parser().parse_args(_publication_arguments())
    assert backtest_execution_fingerprint(arguments, quote_currency="USD") != (
        backtest_execution_fingerprint(arguments, quote_currency="USDC")
    )
