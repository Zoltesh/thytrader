"""Generated operator payload contracts cannot silently lag their HTTP models."""

from __future__ import annotations

import json
from pathlib import Path
import runpy
import sys
from typing import TYPE_CHECKING

import pytest

from thytrader.operator.http import _REPORT_MODELS
from thytrader.operator.models import REPORT_KINDS

if TYPE_CHECKING:
    from collections.abc import Iterator

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts/export_operator_schema.py"


def test_committed_schema_is_current(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every HTTP report payload change must ship its generated schema in the same commit."""
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT), "--check"])
    with pytest.raises(SystemExit) as checked:
        runpy.run_path(str(_SCRIPT), run_name="__main__")
    assert checked.value.code == 0


def _references(value: object) -> Iterator[str]:
    """Walk generated JSON references without assuming a report's nesting depth."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref" and isinstance(child, str):
                yield child
            else:
                yield from _references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _references(child)


def test_schema_export_and_drift_check_are_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Generate in isolation, resolve every local ref, and reject changed or missing output."""
    output = tmp_path / "operator-schema.json"
    invocation = [str(_SCRIPT), "--output", str(output)]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", invocation)
    with pytest.raises(SystemExit) as written:
        runpy.run_path(str(_SCRIPT), run_name="__main__")
    assert written.value.code == 0
    schema = json.loads(output.read_text())
    assert set(schema["properties"]["report_kind"]["enum"]) == set(REPORT_KINDS)
    assert len(schema["anyOf"]) == len(set(_REPORT_MODELS.values()))
    definitions = schema["$defs"]
    for reference in _references(schema):
        assert reference.startswith("#/$defs/")
        assert reference.removeprefix("#/$defs/") in definitions
    assert "WatchedTail" in definitions
    assert "DataHealthReport" in definitions
    assert "payload" in schema["required"]
    monkeypatch.setattr(sys, "argv", [*invocation, "--check"])
    with pytest.raises(SystemExit) as checked:
        runpy.run_path(str(_SCRIPT), run_name="__main__")
    assert checked.value.code == 0
    output.write_text("{}\n")
    with pytest.raises(SystemExit) as changed:
        runpy.run_path(str(_SCRIPT), run_name="__main__")
    assert changed.value.code == 1
    assert "Stale generated operator schema" in capsys.readouterr().err
    output.unlink()
    with pytest.raises(SystemExit) as missing:
        runpy.run_path(str(_SCRIPT), run_name="__main__")
    assert missing.value.code == 1
