"""Cross-language acceptance schema and golden vectors are data exposed by the public CLI."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

from tests.test_replay_cli import cli


def test_shipped_usdc_lifecycle_has_independent_economics(tmp_path: Path) -> None:
    """One frozen data vector covers entry, trailing, partial loss and repeated-fill idempotency."""
    fixtures = Path("tests/replay/fixtures")
    source = fixtures / "usdc-lifecycle.input.json"
    assert source.is_file(), "portable end-to-end fixture is missing"
    package = json.loads(source.read_text())
    assert package["strategy"]["instrument"]["quote_currency"] == "USDC"
    exported = cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    assert exported.returncode == 0, exported.stderr
    manifest = json.loads(exported.stdout)["manifest"]
    checked = cli("run", manifest, "--expected", str(fixtures / "usdc-lifecycle.expected.json"))
    assert checked.returncode == 0, checked.stderr
    report = json.loads(checked.stdout)
    assert report["execution"]["status"] == "match"
    steps = report["execution"]["steps"]
    assert Decimal(steps[0]["actual"]["orders"][0]["quantity"]) == Decimal("2")
    assert Decimal(steps[0]["actual"]["orders"][0]["price"]) == Decimal("10")
    assert Decimal(steps[1]["actual"]["cash"]) == Decimal("-20.1")
    assert Decimal(steps[2]["actual"]["positions"][0]["stop_price"]) == Decimal("9.5")
    assert Decimal(steps[3]["actual"]["cash"]) == Decimal("-15.51")
    assert Decimal(steps[3]["actual"]["positions"][0]["quantity"]) == Decimal("1.5")
    assert Decimal(steps[4]["actual"]["cash"]) == Decimal("-15.51"), (
        "repeated venue fill applied twice"
    )
    assert Decimal(steps[-1]["actual"]["cash"]) == Decimal("-1.99")
    assert steps[-1]["actual"]["positions"] == []
    assert report["ledger"]["ending_quantity"] == "0"
    assert report["ledger"]["total_fees"] == "0.14"
    assert report["ledger"]["realized_net_pnl"] == "-1.99"


def test_checked_in_schemas_equal_public_cli() -> None:
    """Rust and other consumers have checked-in contracts identical to the shipped exporter."""
    for kind in ("package", "manifest", "report"):
        path = Path("tests/replay/schemas") / f"{kind}.schema.json"
        assert path.is_file(), f"portable {kind} schema missing"
        assert json.loads(path.read_text()) == json.loads(cli("schema", "--kind", kind).stdout)


def test_public_cli_exports_language_neutral_schema() -> None:
    """Foreign implementations consume JSON Schema, not Python imports or monkeypatches."""
    result = cli("schema", "--kind", "package")
    assert result.returncode == 0, result.stderr
    schema = json.loads(result.stdout)
    assert schema["properties"]["schema_version"]["const"] == "thytrader-replay-v1"
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["LedgerEvent"]["properties"]["quantity"]["type"] == "string"


def test_cli_expected_report_is_checked_independently(tmp_path: Path) -> None:
    """A tampered golden report produces a mismatch instead of accepting its own expectations."""
    exported = cli(
        "export",
        "--input",
        "tests/replay/fixtures/partial-loss.input.json",
        "--root",
        str(tmp_path / "packages"),
    )
    assert exported.returncode == 0, exported.stderr
    manifest = json.loads(exported.stdout)["manifest"]
    first = cli("run", manifest)
    assert first.returncode == 3, first.stderr
    expected = json.loads(first.stdout)
    expected["ledger"]["ending_cash"] = "0"
    path = tmp_path / "expected.json"
    path.write_text(json.dumps(expected), encoding="utf-8")
    checked = cli("run", manifest, "--expected", str(path))
    assert checked.returncode == 1, checked.stderr
    report = json.loads(checked.stdout)
    assert report["acceptance"]["status"] == "mismatch"
    assert "ledger.ending_cash" in report["acceptance"]["differences"]
