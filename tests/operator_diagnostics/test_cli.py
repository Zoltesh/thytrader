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
    assert "runtime" in output
    assert "schema-check" in output
    assert "loopback HTTP" in output or "--local" in output


def test_operator_health_emits_json_and_nonzero_without_stack(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Health JSON is still a v1 report when local workers are not running."""
    with pytest.raises(SystemExit) as raised:
        main(["--local", "health"])
    assert raised.value.code in {1, 2}
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["report_kind"] == "health"
    assert payload["overall_status"] in {"degraded", "failed"}


def test_operator_rejects_non_loopback_base_url() -> None:
    """HTTP mode must refuse a remote origin instead of falling back to Postgres."""
    with pytest.raises(SystemExit) as raised:
        main(["--base-url", "http://example.com", "health"])
    assert raised.value.code != 0
    assert "loopback" in str(raised.value).lower()


def test_operator_http_failure_does_not_fall_back_to_local_stores() -> None:
    """An unreachable API must not silently open process stores."""
    with pytest.raises(SystemExit) as raised:
        main(["--base-url", "http://127.0.0.1:1", "health"])
    assert raised.value.code != 0
    assert "unreachable" in str(raised.value).lower()


def test_operator_rejects_local_and_base_url_together() -> None:
    """HTTP and store-backed modes are exclusive."""
    with pytest.raises(SystemExit) as raised:
        main(["--local", "--base-url", "http://127.0.0.1:8200", "health"])
    assert raised.value.code != 0
    assert "either" in str(raised.value).lower()


def test_operator_schema_check_passes_in_this_checkout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """schema-check is the compatibility gate between skills and SCHEMA_VERSION."""
    with pytest.raises(SystemExit) as raised:
        main(["schema-check"])
    assert raised.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["schema_version"] == SCHEMA_VERSION
    assert "runtime" in payload["report_kinds"]
