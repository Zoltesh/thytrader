"""CLI surfaces of the paper futures runtime lane (ADR 0129, P1-6).

``thytrader-runtime start`` refuses live futures before any HTTP call, ``show`` adds the
``futures`` view to a futures bot (and degrades truthfully when it cannot be read), and
``thytrader-operator futures-books`` is a validated read-only report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from unittest.mock import patch

import pytest

from tests.http_fakes import matching_ready_payload, orchestration_status_payload, urlopen_by_path
from thytrader.agent_http import AgentHttpError
from thytrader.operator.cli import main as operator_main
from thytrader.operator.futures_books_report import FuturesBooksReport, build_futures_books_report
from thytrader.operator.http import fetch_operator_report
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.runtime_control.cli import main as runtime_main
from thytrader.runtime_control.inventory_commands import run_inventory_read
from thytrader.trading.memory import InMemoryExecutionStore

_STRATEGY = "01985cf0-7b60-7000-8000-00000000c0de"
_BOT = "01985cf0-7b60-7000-8000-00000000b0b0"
_BASE = "http://127.0.0.1:8000"


def _show() -> argparse.Namespace:
    """Parsed ``show <bot>`` arguments."""
    return argparse.Namespace(command="show", deployment_id=_BOT, detail="summary")


def test_show_adds_the_futures_view_to_a_futures_bot_only() -> None:
    """A futures bot gains ``futures``; a spot bot is returned unchanged with one read."""
    module = "thytrader.runtime_control.inventory_commands"
    view = {"product_id": "BIP-20DEC30-CDE", "contracts": "3", "liquidation_price": "62.5"}
    with (
        patch(f"{module}.show_deployment", return_value={"product_id": "BIP-20DEC30-CDE"}),
        patch(f"{module}.show_deployment_futures", return_value=view) as futures,
    ):
        shown = run_inventory_read(_show(), _BASE)
    assert shown == {"product_id": "BIP-20DEC30-CDE", "futures": view}
    futures.assert_called_once_with(_BASE, _BOT)
    with (
        patch(f"{module}.show_deployment", return_value={"product_id": "BTC-USDC"}),
        patch(f"{module}.show_deployment_futures") as futures,
    ):
        assert run_inventory_read(_show(), _BASE) == {"product_id": "BTC-USDC"}
    futures.assert_not_called()


def test_show_reports_an_unreadable_futures_view_instead_of_inventing_it() -> None:
    """A failed futures read keeps the bot and says so; figures are never filled in."""
    module = "thytrader.runtime_control.inventory_commands"
    with (
        patch(f"{module}.show_deployment", return_value={"product_id": "BIP-20DEC30-CDE"}),
        patch(f"{module}.show_deployment_futures", side_effect=AgentHttpError("API down")),
    ):
        shown = run_inventory_read(_show(), _BASE)
    assert shown == {
        "product_id": "BIP-20DEC30-CDE",
        "futures": None,
        "futures_error": "API down",
    }


def test_live_start_with_a_per_contract_fee_is_refused_before_http() -> None:
    """``--fee-per-contract`` is paper futures only; live never reaches the API."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.runtime_control.deployment_handlers.start_deployment") as request,
        pytest.raises(SystemExit) as raised,
    ):
        runtime_main(
            [
                "start",
                "--strategy-id",
                _STRATEGY,
                "--mode",
                "live",
                "--fee-per-contract",
                "0.15",
                "--confirm",
                "--i-understand-live",
            ]
        )
    assert raised.value.code != 0
    assert "FUTURES_LIVE_UNSUPPORTED" in str(raised.value)
    request.assert_not_called()


def test_paper_futures_start_forwards_all_three_fees() -> None:
    """The paper start carries maker, taker and the per-contract fee to the API."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch(
            "thytrader.runtime_control.deployment_handlers.start_deployment",
            return_value={"id": _BOT, "mode": "paper"},
        ) as request,
        pytest.raises(SystemExit) as raised,
    ):
        runtime_main(
            [
                "start",
                "--strategy-id",
                _STRATEGY,
                "--mode",
                "paper",
                "--cash",
                "10000",
                "--maker-fee-rate",
                "0",
                "--taker-fee-rate",
                "0.0005",
                "--fee-per-contract",
                "0.15",
                "--confirm",
            ]
        )
    assert raised.value.code == 0
    arguments = request.call_args.kwargs
    assert (arguments["maker_fee_rate"], arguments["taker_fee_rate"]) == ("0", "0.0005")
    assert arguments["paper_fee_per_contract"] == "0.15"
    assert arguments["mode"] == "paper"


def test_help_documents_the_futures_lane(capsys: pytest.CaptureFixture[str]) -> None:
    """Operators discover futures-books, the show view and the live refusal from --help."""
    with pytest.raises(SystemExit):
        operator_main(["--help"])
    assert "futures-books" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        runtime_main(["start", "--help"])
    start_help = capsys.readouterr().out
    assert "--fee-per-contract" in start_help
    assert "FUTURES_LIVE_UNSUPPORTED" in start_help
    with pytest.raises(SystemExit):
        runtime_main(["show", "--help"])
    assert "futures" in capsys.readouterr().out


def test_the_operator_client_validates_a_futures_books_report() -> None:
    """``futures-books`` is a registered report the HTTP CLI validates."""
    report = asyncio.run(
        build_futures_books_report(
            execution=InMemoryExecutionStore(),
            journal=None,
            contracts=None,
            observations=None,
            policy=compiled_default_risk_policy(),
        )
    )
    payload = json.loads(report.model_dump_json())
    with patch("thytrader.operator.http.request_json", return_value=payload) as request:
        fetched = fetch_operator_report(base_url=_BASE, command="futures-books")
    assert isinstance(fetched, FuturesBooksReport)
    assert fetched.payload.futures_policy_set is False
    assert request.call_args.kwargs["url"] == f"{_BASE}/api/v1/operator/futures-books"
