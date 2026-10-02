"""Tests for the agent-CLI unexpected-failure description."""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError
import pytest

from thytrader.cli_errors import describe_unexpected_failure

_FAKE_KEY_NAME = "organizations/test-org/apiKeys/test-key-name-not-real"
_FAKE_PRIVATE_KEY = (
    "-----BEGIN EC PRIVATE KEY-----\\nhermetic-test-only\\n-----END EC PRIVATE KEY-----"
)


class _Shape(BaseModel):
    """A model whose validation failure names one field."""

    report_kind: str


def _validation_error() -> ValidationError:
    """Return the ValidationError for a payload missing ``report_kind``."""
    try:
        _Shape.model_validate({})
    except ValidationError as error:
        return error
    raise AssertionError("expected a validation error")


def test_unexpected_failure_names_the_lane_cause_safety_and_next_step() -> None:
    """The message keeps the safety statement and says what failed."""
    message = describe_unexpected_failure(
        RuntimeError("worker lease table is locked\nsecond line"),
        lane="Research command",
        safety="Paper and live state were not changed.",
    )
    assert message.startswith(
        "Research command failed: unexpected RuntimeError: worker lease table is locked."
    )
    assert "second line" not in message
    assert "Paper and live state were not changed." in message
    assert "thytrader-operator health" in message


def test_schema_mismatch_names_the_field_and_the_rebuild() -> None:
    """A payload the CLI cannot validate names the field and the stale-image remedy."""
    message = describe_unexpected_failure(
        _validation_error(), lane="Operator diagnostics", safety="Trading state was not changed."
    )
    assert "did not match this CLI's schema (report_kind: Field required)" in message
    assert "make run" in message


def test_file_and_json_errors_name_the_input() -> None:
    """Unreadable paths and malformed JSON say which input failed and where."""
    missing = describe_unexpected_failure(
        FileNotFoundError(2, "No such file or directory", "study.json"),
        lane="Research command",
        safety="Paper and live state were not changed.",
    )
    assert "could not read study.json (No such file or directory)" in missing
    with pytest.raises(json.JSONDecodeError) as raised:
        json.loads('{"kind": ')
    broken = describe_unexpected_failure(raised.value, lane="Research command", safety="")
    assert "input is not valid JSON (Expecting value at line 1 column 10)" in broken


def test_unexpected_failure_redacts_configured_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret that leaks into an exception message never reaches agent output."""
    monkeypatch.setenv("THYTRADER_COINBASE_API_KEY_NAME", _FAKE_KEY_NAME)
    monkeypatch.setenv("THYTRADER_COINBASE_API_PRIVATE_KEY", _FAKE_PRIVATE_KEY)
    message = describe_unexpected_failure(
        RuntimeError(f"bad key {_FAKE_KEY_NAME}"),
        lane="Runtime command",
        safety="Inspect deployments before retrying.",
    )
    assert _FAKE_KEY_NAME not in message
    assert "bad key" in message
