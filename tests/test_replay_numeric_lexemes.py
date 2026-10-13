"""Raw nested financial spellings retain their budget before Decimal normalization."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from thytrader.replay.models import ReplayPackage, canonical_bytes, fingerprint
from thytrader.replay.numeric import validate_numeric_inputs
from thytrader.replay.packages import load, read_input

ORIGINAL_BYPASSES = [
    pytest.param(" " * 196 + "1", id="whitespace"),
    pytest.param("0_" * 198 + "1", id="underscored-zeroes"),
    pytest.param("\u0660" * 196 + "\u0661", id="unicode-digits"),
    pytest.param("_" * 1000100 + "1", id="million-underscores"),
]


def nested_input(tmp_path: Path, field: str, text: str) -> Path:
    """Change only a synthetic financial field, keeping candle containment coherent."""
    payload = json.loads(Path("tests/replay/fixtures/no-signal.input.json").read_text())
    step = payload["steps"][0]
    if field == "marks":
        step["marks"] = [["BTC-USD", text]]
    elif field == "base_increment":
        step["product"]["base_increment"] = text
    else:
        assert field == "ohlc"
        for key in ("open", "high", "low", "close"):
            step["candles"][0][key] = text
    source = tmp_path / "input.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    return source


def installed_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Exercise a fresh installed console process without application database settings."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "THYTRADER_DATABASE_URL",
            "THYTRADER_TEST_DATABASE_URL",
            "THYTRADER_INTEGRATION_DATABASE_URL",
        }
    }
    return subprocess.run(  # noqa: S603 - installed entry point and synthetic test arguments only
        [str(Path(sys.executable).parent / "thytrader-replay"), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("field", ["marks", "base_increment", "ohlc"])
@pytest.mark.parametrize("text", ORIGINAL_BYPASSES)
def test_nested_financial_lexical_budget(tmp_path: Path, field: str, text: str) -> None:
    """Raw padding cannot disappear into a valid typed one before lexical admission."""
    source = nested_input(tmp_path, field, text)
    with pytest.raises(ValueError, match="lexical budget"):
        canonical_bytes(read_input(source))


@pytest.mark.parametrize("text", ORIGINAL_BYPASSES)
def test_installed_export_refuses_raw_lexical_bypass(tmp_path: Path, text: str) -> None:
    """The installed CLI refuses before publication with the stable redacted envelope."""
    source = nested_input(tmp_path, "marks", text)
    root = tmp_path / "packages"
    result = installed_cli("export", "--input", str(source), "--root", str(root))
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "thytrader-replay: evidence rejected or unavailable\n"
    assert not root.exists()


@pytest.mark.parametrize("text", ["NaN", "+nAn\u0661", "-sNaN001", "INF", "+infinity", "-Infinity"])
def test_nonfinite_aliases_are_preflighted_before_conversion(text: str) -> None:
    """Even rejected nonfinite aliases must not reach an unbounded downstream constructor."""
    with pytest.raises(ValueError, match="finite Decimal envelope"):
        validate_numeric_inputs({"nested": [("amount", "\u2003_" + text + "_\u00a0")]})
    with pytest.raises(ValueError, match="lexical budget"):
        validate_numeric_inputs({"nested": [("amount", "_" * 196 + text)]})


@pytest.mark.parametrize(
    "text",
    [
        "1",
        "+1",
        "01",
        "1.",
        ".1E1",
        "1e+0",
        "1E-0",
        "\u0661",
        "\uff11",
        "\U0001d7d9",
        "\u0661.\u0660E+\u0660",
        "_1",
        "1_",
        "__+_1_._0_E_+_0__",
        "\u2003\t_1_\n\u00a0",
        "0" * 195 + "1",
    ],
)
@pytest.mark.parametrize("field", ["marks", "base_increment", "ohlc"])
def test_short_equivalent_nested_spellings_remain_valid(
    tmp_path: Path, field: str, text: str
) -> None:
    """Recognition must not narrow the existing finite Decimal adapter grammar."""
    package = read_input(nested_input(tmp_path, field, text))
    baseline = read_input(nested_input(tmp_path, field, "1"))
    assert canonical_bytes(package) == canonical_bytes(baseline)
    if field == "marks":
        # Independent public vector: the original synthetic fixture with a unit mark.
        assert fingerprint(canonical_bytes(package)) == (
            "sha256:611c45a36c4eda8b04b91c22895338d11fc3aa17f00df2952ef5bf8eb1a1cef8"
        )


@pytest.mark.parametrize(
    "text",
    [
        "1" + "_" * 196,
        "_" * 196 + "+1",
        "0" * 196 + "1",
        "1" + "\u2003" * 196,
        "1e+" + "_" * 196 + "\u0660",
        "\u0660" * 196 + "\u0661",
        "0" * 194 + ".1E1",
    ],
)
def test_complete_finite_family_keeps_original_character_budget(text: str) -> None:
    """Signs, dots, exponent digits and ignored suffixes count before normalization."""
    with pytest.raises(ValueError, match="lexical budget"):
        validate_numeric_inputs({"nested": [("amount", text)]})


@pytest.mark.parametrize(
    "label",
    [
        "1-build-label-" * 30,
        "NaN-build-" * 30,
        "Infinity-release-" * 30,
        "Inf" + "0" * 196,
        "\u0130nf" + "_" * 196,
        "2026-01-01T00:00:00Z" * 30,
        "00000000-0000-4000-8000-000000000001" * 10,
        "_ 1_" * 100,
        "123²" * 100,
    ],
)
def test_long_nonnumeric_labels_are_not_decimal_inputs(label: str) -> None:
    """Date/UUID/build labels and non-Nd digits remain metadata rather than amounts."""
    payload = json.loads(Path("tests/replay/fixtures/no-signal.input.json").read_text())
    payload["source"]["execution_build"] = label
    package = ReplayPackage.model_validate_json(json.dumps(payload))
    assert (
        ReplayPackage.model_validate_json(canonical_bytes(package)).source.execution_build == label
    )


@pytest.mark.parametrize("text", ["0" * 195 + "1", "\u2003" * 195 + "1", "_" * 195 + "1"])
def test_installed_inclusive_lexical_boundary(tmp_path: Path, text: str) -> None:
    """Exactly 196 raw characters export, inspect and read back as the same unit mark."""
    source = nested_input(tmp_path, "marks", text)
    result = installed_cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    assert result.returncode == 0, result.stderr
    publication = json.loads(result.stdout)
    manifest = Path(publication["manifest"])
    assert installed_cli("inspect", str(manifest)).returncode == 0
    package, checked = load(manifest)
    assert str(package.steps[0].marks[0][1]) == "1"
    assert checked.fingerprint == (
        "sha256:611c45a36c4eda8b04b91c22895338d11fc3aa17f00df2952ef5bf8eb1a1cef8"
    )
