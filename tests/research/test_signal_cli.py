"""Tests for the read-only HTTP signal-trace command (ADR 0090)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from thytrader.agent_http import AgentHttpError
from thytrader.research import cli
from thytrader.research.cli import main

if TYPE_CHECKING:
    from collections.abc import Callable

_RESULT = "sha256:" + "b" * 64
_RUN = "sha256:" + "a" * 64
_BASE = "http://127.0.0.1:8200"
_PAGE: dict[str, object] = {"result_fingerprint": _RESULT, "records": [], "total_records": 0}


def _serve(monkeypatch: pytest.MonkeyPatch, handler: Callable[[str], object]) -> list[str]:
    """Route the CLI's GETs to ``handler`` and skip the ops-contract probe."""
    seen: list[str] = []

    def fake_request_json(*, method: str, url: str) -> object:
        assert method == "GET"
        seen.append(url)
        return handler(url)

    def fake_contract(base_url: str) -> None:
        assert base_url.startswith("http://127.0.0.1")

    monkeypatch.setattr(cli, "request_json", fake_request_json)
    monkeypatch.setattr(cli, "require_matching_ops_contract", fake_contract)
    return seen


def _page(url: str) -> object:
    """Serve one fixed trace page for any URL."""
    del url
    return _PAGE


def test_cli_help_describes_the_read_only_http_trace(capsys: pytest.CaptureFixture[str]) -> None:
    """Operators discover the result/run argument and the bounded page flags."""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    for expected in ("result_fingerprint", "run_fingerprint", "--outcome", "--cursor", "--pretty"):
        assert expected in output
    assert "Read-only" in output


def test_cli_prints_the_trace_page_for_a_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A result fingerprint maps straight onto the signal-trace route."""
    seen = _serve(monkeypatch, _page)
    main([_RESULT, "--outcome", "matched", "--limit", "5", "--base-url", _BASE])
    assert json.loads(capsys.readouterr().out) == _PAGE
    assert seen == [f"{_BASE}/api/v1/backtests/{_RESULT}/signal-trace?outcome=matched&limit=5"]


def test_cli_resolves_a_run_fingerprint_to_its_newest_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 404 on the trace route retries through the run's newest published result."""

    def handler(url: str) -> object:
        if f"/backtests/{_RUN}/signal-trace" in url:
            raise AgentHttpError("not found", status=404)
        if "run_fingerprint=" in url:
            return {"entries": [{"result_fingerprint": _RESULT}]}
        return _PAGE

    seen = _serve(monkeypatch, handler)
    main([_RUN, "--base-url", _BASE])
    assert json.loads(capsys.readouterr().out) == _PAGE
    assert f"/backtests/{_RESULT}/signal-trace" in seen[-1]


def test_cli_explains_an_unknown_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    """No result and no run: the operator learns which fingerprint to pass."""

    def handler(url: str) -> object:
        if "/signal-trace" in url:
            raise AgentHttpError("not found", status=404)
        return {"entries": []}

    _serve(monkeypatch, handler)
    with pytest.raises(SystemExit) as raised:
        main([_RUN, "--base-url", _BASE])
    assert "No backtest result has this fingerprint" in str(raised.value)


def test_cli_surfaces_the_api_reason_instead_of_a_generic_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A redacted API failure reaches the operator verbatim, not as an opaque message."""

    def handler(url: str) -> object:
        del url
        raise AgentHttpError("The re-evaluated signal trace does not match.", status=503)

    _serve(monkeypatch, handler)
    with pytest.raises(SystemExit) as raised:
        main([_RESULT, "--base-url", _BASE])
    assert "does not match" in str(raised.value)
    assert "failed safely" not in str(raised.value)
