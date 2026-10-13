"""Portable acceptance tests exercise only the shipped offline replay CLI."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
import json
from pathlib import Path

from thytrader.replay.cli import main


@dataclass(frozen=True)
class CliResult:
    """Observable public CLI contract, without importing engine or store internals."""

    returncode: int
    stdout: str
    stderr: str


def cli(*args: str) -> CliResult:
    """Drive the shipped CLI entry point and JSON contract in an isolated event-loop thread.

    External console-script invocation is also proved separately in release artifacts.
    The thread permits use by async worker tests without nesting asyncio.run().
    """

    def invoke() -> CliResult:
        """Capture exactly what an installed entry point writes and returns."""
        output, error = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            try:
                code = main(args)
            except SystemExit as failure:
                code = failure.code if isinstance(failure.code, int) else 1
        return CliResult(code, output.getvalue(), error.getvalue())

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(invoke).result(timeout=30)


def test_immutable_export_is_inspectable(tmp_path: Path) -> None:
    """An independent data document publishes and reads back with an immutable identity."""
    source = tmp_path / "source.json"
    source.write_text(
        Path("tests/replay/fixtures/partial-loss.input.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    exported = cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    assert exported.returncode == 0, exported.stderr
    publication = json.loads(exported.stdout)
    inspected = cli("inspect", publication["manifest"])
    assert inspected.returncode == 0, inspected.stderr
    report = json.loads(inspected.stdout)
    assert report["fingerprint"] == publication["fingerprint"]
    assert report["evidence_kind"] == "synthetic"
    assert report["execution_completeness"] == "unavailable"


def test_partial_loss_ledger_reproduces_without_certifying_execution(tmp_path: Path) -> None:
    """Cashflow reconstruction is known while absent market/broker comparisons stay unknown."""
    exported = cli(
        "export",
        "--input",
        "tests/replay/fixtures/partial-loss.input.json",
        "--root",
        str(tmp_path / "packages"),
    )
    assert exported.returncode == 0, exported.stderr
    manifest = json.loads(exported.stdout)["manifest"]
    run = cli("run", manifest)
    assert run.returncode == 3, run.stderr
    report = json.loads(run.stdout)
    assert report["ledger"]["ending_cash"] == "-3.02"
    assert report["ledger"]["ending_quantity"] == "0"
    assert report["ledger"]["total_fees"] == "0.27"
    assert report["ledger"]["realized_net_pnl"] == "-3.02"
    assert report["ledger"]["status"] == "match"
    assert report["same_config_research_execution"]["status"] == "unavailable"
    assert report["original_research_deployed"]["status"] == "unavailable"


def test_altered_ledger_expectation_returns_mismatch(tmp_path: Path) -> None:
    """Changing an expected cash fact cannot make the independently replayed ledger pass."""
    payload = json.loads(Path("tests/replay/fixtures/partial-loss.input.json").read_text())
    payload["ledger"]["observed_ending_cash"] = "-1"
    source = tmp_path / "altered.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    exported = cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    assert exported.returncode == 0, exported.stderr
    result = cli("run", json.loads(exported.stdout)["manifest"], "--format", "text")
    assert result.returncode == 1, result.stderr
    assert "-3.02" in result.stdout
    assert "mismatch" in result.stdout
    assert "Observed" in result.stdout


def test_future_candle_is_rejected_before_publication(tmp_path: Path) -> None:
    """An observation cannot include candles that had not completed when data became available."""
    payload = json.loads(Path("tests/replay/fixtures/no-signal.input.json").read_text())
    payload["steps"][0]["candles"][-1]["starts_at"] = "2099-01-01T00:00:00Z"
    source = tmp_path / "future.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    result = cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    assert result.returncode == 2, result.stderr
    assert not (tmp_path / "packages").exists()


def test_nested_unknown_fields_cannot_be_hidden_by_domain_coercion(tmp_path: Path) -> None:
    """An existing domain dataclass is not permission to discard unrecognized capture fields."""
    payload = json.loads(Path("tests/replay/fixtures/no-signal.input.json").read_text())
    payload["steps"][0]["before"]["deployment"]["unexpected_transport_payload"] = "forbidden"
    source = tmp_path / "unknown.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    result = cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    assert result.returncode == 2, result.stderr


def test_decimal_capture_requires_strings_not_json_numbers(tmp_path: Path) -> None:
    """The language-neutral financial contract refuses numeric coercion before fingerprinting."""
    payload = json.loads(Path("tests/replay/fixtures/no-signal.input.json").read_text())
    payload["steps"][0]["before"]["deployment"]["cash"] = 10000
    source = tmp_path / "numeric.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    result = cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    assert result.returncode == 2, result.stderr


def test_original_rules_are_distinct_from_same_config_replay(tmp_path: Path) -> None:
    """A known sizing edit cannot be certified by matching signals on the deployed snapshot."""
    payload = json.loads(Path("tests/replay/fixtures/no-signal.input.json").read_text())
    payload["original_strategy"] = json.loads(json.dumps(payload["strategy"]))
    payload["original_strategy"]["sizing"]["risk_fraction"] = "0.01"
    source = tmp_path / "sizing-change.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    exported = cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    assert exported.returncode == 0, exported.stderr
    result = cli("run", json.loads(exported.stdout)["manifest"])
    report = json.loads(result.stdout)
    assert report["original_research_deployed"]["status"] == "mismatch"
    assert report["same_config_research_execution"]["status"] == "match"
    assert "sizing.risk_fraction" in report["configuration_differences"]


def test_altered_execution_expectation_returns_mismatch_exit(tmp_path: Path) -> None:
    """The CLI reruns orchestration and rejects an altered expected ending book."""
    payload = json.loads(Path("tests/replay/fixtures/no-signal.input.json").read_text())
    payload["steps"][0]["expected"]["cash"] = "9999"
    source = tmp_path / "wrong-expectation.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    exported = cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    assert exported.returncode == 0, exported.stderr
    result = cli("run", json.loads(exported.stdout)["manifest"])
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout)["execution"]["status"] == "mismatch"


def test_nonfinite_starting_book_is_rejected_before_fingerprinting(tmp_path: Path) -> None:
    """Historical absence is allowed, but forged known NaN cash cannot become immutable evidence."""
    payload = json.loads(Path("tests/replay/fixtures/no-signal.input.json").read_text())
    payload["steps"][0]["before"]["deployment"]["cash"] = "NaN"
    source = tmp_path / "nan.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    result = cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    assert result.returncode == 2, result.stderr
    assert not (tmp_path / "packages").exists()


def test_decimal_spelling_does_not_move_canonical_identity(tmp_path: Path) -> None:
    """Canonical Decimal rendering is lexical and preserves exact semantic identity."""
    payload = json.loads(Path("tests/replay/fixtures/no-signal.input.json").read_text())
    first = cli(
        "export",
        "--input",
        "tests/replay/fixtures/no-signal.input.json",
        "--root",
        str(tmp_path / "first"),
    )
    assert first.returncode == 0, first.stderr
    payload["steps"][0]["before"]["deployment"]["cash"] = "10000.000"
    source = tmp_path / "equivalent.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    second = cli("export", "--input", str(source), "--root", str(tmp_path / "second"))
    assert second.returncode == 0, second.stderr
    assert json.loads(first.stdout)["fingerprint"] == json.loads(second.stdout)["fingerprint"]
