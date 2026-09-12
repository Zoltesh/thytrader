"""CLI tests for confirmation-gated research mutations."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from thytrader.agent_http import AgentHttpError
from thytrader.research.mutation_cli import main

_REFERENCE_STRATEGY = (
    Path(__file__).parents[1] / "strategies" / "golden" / "reference_strategy_v1.json"
)


def test_research_help_mentions_confirm_and_no_trading(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can discover the confirmation gate without a database."""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "--confirm" in output
    assert "no paper or live" in output.lower() or "no paper" in output.lower()


def test_create_draft_help_allows_five_minute_paper(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """create-draft help must not claim paper stays 1h-only."""
    with pytest.raises(SystemExit) as raised:
        main(["create-draft", "--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out.lower()
    assert "paper may be 1h or 5m" in output
    assert "live stays 1h" in output


def test_create_draft_without_confirm_does_not_write() -> None:
    """Omitting --confirm must exit before any research mutation."""
    with pytest.raises(SystemExit) as raised:
        main(["create-draft"])
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)


def test_save_draft_prints_crossover_validation_error(tmp_path: Path) -> None:
    """Invalid crossover operands must surface the semantic validator message."""
    payload = json.loads(_REFERENCE_STRATEGY.read_text())
    payload["entry"]["when"]["all"][0]["right"] = {"literal": "50"}
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(SystemExit) as raised:
        main(["save-draft", "--file", str(path), "--revision", "1", "--confirm"])
    assert "crossover right operand must reference an indicator" in str(raised.value)
    assert "failed safely" not in str(raised.value)


def test_submit_backtest_stale_engine_422_hints_rebuild(tmp_path: Path) -> None:
    """A stale API that still only names v1/v2 must tell operators to rebuild."""
    path = tmp_path / "request.json"
    path.write_text("{}")
    with (
        patch(
            "thytrader.research.mutation_cli.BacktestSubmissionRequest.model_validate",
            return_value=object(),
        ),
        patch(
            "thytrader.research.http.submit_backtest",
            side_effect=AgentHttpError(
                "HTTP 422: Input should be 'thytrader-bar-backtest-v1' or "
                "'thytrader-bar-backtest-v2'"
            ),
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main(["submit-backtest", "--file", str(path), "--confirm"])
    message = str(raised.value)
    assert "422" in message
    assert "make run" in message
    assert "failed safely" not in message
