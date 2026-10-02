"""``thytrader-runtime portfolio-*`` commands (ADR 0091)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.http_fakes import matching_ready_payload, orchestration_status_payload, urlopen_by_path
from thytrader.runtime_control.cli import main

_PID = "01a0f000-0000-7000-8000-000000000001"
_SLEEVE = "01a0f000-0000-7000-8000-000000000201"
_STRATEGY = "01a0f000-0000-7000-8000-000000000101"
_MODULE = "thytrader.runtime_control.portfolio_commands"


def _portfolio(mode: str) -> dict[str, object]:
    """The fields the portfolio commands read from GET /api/v1/portfolios/{id}."""
    return {
        "portfolio_id": _PID,
        "mode": mode,
        "revision": 4,
        "sleeves": [{"sleeve_id": _SLEEVE, "strategy_id": _STRATEGY}],
    }


def _handlers(*, tiers: tuple[str, ...] = ()) -> dict[str, object]:
    """Ready plus the YOLO status."""
    return {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(
            yolo_enabled=bool(tiers), yolo_tiers=tiers
        ),
        "POST /api/v1/agent-orchestration/skipped-confirmations": {
            "id": "11111111-1111-1111-1111-111111111111",
            "category": "runtime",
            "action": "confirm_skipped",
            "outcome": "info",
            "tier": "paper",
            "command": "portfolio-pause",
        },
    }


def test_help_lists_the_portfolio_commands(capsys: pytest.CaptureFixture[str]) -> None:
    """Operators discover portfolio deployment from --help."""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    for command in ("portfolio-start", "portfolio-pause", "portfolio-reset-breaker"):
        assert command in output


def test_live_start_needs_the_acknowledgement_before_anything_else() -> None:
    """--i-understand-live is checked before YOLO and before the start request."""
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(_handlers())),
        patch(f"{_MODULE}.show_portfolio", return_value=_portfolio("live")),
        patch(f"{_MODULE}.start_portfolio") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["portfolio-start", "--portfolio-id", _PID, "--revision", "4", "--confirm"])
    assert "--i-understand-live" in str(raised.value)
    request.assert_not_called()


def test_start_forwards_the_revision_sleeve_and_acknowledgement() -> None:
    """A strategy id given as --sleeve-id resolves to the sleeve."""
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(_handlers())),
        patch(f"{_MODULE}.show_portfolio", return_value=_portfolio("live")),
        patch(f"{_MODULE}.start_portfolio", return_value={"action": "start"}) as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(
            [
                "portfolio-start",
                "--portfolio-id",
                _PID,
                "--revision",
                "4",
                "--sleeve-id",
                _STRATEGY,
                "--confirm",
                "--i-understand-live",
            ]
        )
    assert raised.value.code == 0
    kwargs = request.call_args.kwargs
    assert (kwargs["revision"], kwargs["sleeve_id"], kwargs["i_understand_live"]) == (
        4,
        _SLEEVE,
        True,
    )


def test_paper_yolo_skips_confirm_on_a_paper_portfolio_only() -> None:
    """The portfolio's mode picks the YOLO tier, like single deployments."""
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_by_path(_handlers(tiers=("paper",))),
        ),
        patch(f"{_MODULE}.show_portfolio", return_value=_portfolio("paper")),
        patch(f"{_MODULE}.portfolio_action", return_value={"action": "pause"}) as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["portfolio-pause", "--portfolio-id", _PID])
    assert raised.value.code == 0
    request.assert_called_once()
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_by_path(_handlers(tiers=("paper",))),
        ),
        patch(f"{_MODULE}.show_portfolio", return_value=_portfolio("live")),
        patch(f"{_MODULE}.portfolio_action") as live_request,
        pytest.raises(SystemExit) as refused,
    ):
        main(["portfolio-pause", "--portfolio-id", _PID])
    assert "Pass --confirm" in str(refused.value)
    live_request.assert_not_called()


def test_reset_breaker_always_needs_confirm() -> None:
    """YOLO never covers a breaker reset."""
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_by_path(_handlers(tiers=("paper", "live"))),
        ),
        patch(f"{_MODULE}.reset_portfolio_breaker") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["portfolio-reset-breaker", "--portfolio-id", _PID])
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


def test_stop_forwards_flatten() -> None:
    """--flatten maps onto the flatten stop."""
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(_handlers())),
        patch(f"{_MODULE}.show_portfolio", return_value=_portfolio("paper")),
        patch(f"{_MODULE}.portfolio_action", return_value={"action": "stop"}) as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["portfolio-stop", "--portfolio-id", _PID, "--flatten", "--confirm"])
    assert raised.value.code == 0
    assert request.call_args.args[2] == "stop"
    assert request.call_args.kwargs["flatten"] is True
