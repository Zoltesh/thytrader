"""CLI tests for the read-only operator command."""

from __future__ import annotations

import json

import pytest

from thytrader.operator.cli import main
from thytrader.operator.models import SCHEMA_VERSION


def test_operator_help_describes_read_only_commands(capsys: pytest.CaptureFixture[str]) -> None:
    """Operators can discover commands without a database."""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "cancel orders" in output
    assert "health" in output
    assert "support-bundle" in output


def test_operator_health_emits_json_and_nonzero_without_stack(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Health JSON is still a v1 report when local workers are not running."""
    with pytest.raises(SystemExit) as raised:
        main(["health"])
    assert raised.value.code in {1, 2}
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["report_kind"] == "health"
    assert payload["overall_status"] in {"degraded", "failed"}
