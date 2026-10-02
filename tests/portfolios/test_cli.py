"""The confirmation-gated thytrader-portfolio CLI (ADR 0088)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest

from tests.http_fakes import json_urlopen_response, matching_ready_payload, stale_ready_payload
from thytrader.portfolios.cli import main

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_PID = "01a0f000-0000-7000-8000-000000000001"
_SLEEVE = "01a0f000-0000-7000-8000-000000000201"
_STRATEGY = "01a0f000-0000-7000-8000-000000000101"


def _portfolio() -> dict[str, object]:
    """One PortfolioResponse body with a single sleeve."""
    return {
        "portfolio_id": _PID,
        "name": "Core",
        "mode": "paper",
        "quote_currency": "USDC",
        "capital_quote": "1000",
        "cash_reserve_fraction": "0.2",
        "revision": 3,
        "created_at": "2026-10-02T12:00:00Z",
        "updated_at": "2026-10-02T12:00:00Z",
        "limits": {
            "max_total_exposure_fraction": "1",
            "max_per_asset_fraction": "1",
            "daily_loss_quote": None,
            "max_drawdown_fraction": None,
        },
        "manager": {
            "mandate": "",
            "permissions": {
                "may_rebalance": False,
                "max_weight_change_per_week": "0.1",
                "may_pause_sleeves": False,
                "may_propose_sleeves": False,
            },
        },
        "sleeves": [
            {
                "sleeve_id": _SLEEVE,
                "strategy_id": _STRATEGY,
                "strategy_name": "EMA",
                "product_id": "BTC-USDC",
                "covered_product_ids": ["BTC-USDC"],
                "timeframe": "1h",
                "quote_currency": "USDC",
                "strategy_valid": True,
                "current_fingerprint": None,
                "weight_fraction": "0.5",
                "capital_quote": "500",
                "note": None,
                "issues": [],
                "created_at": "2026-10-02T12:00:00Z",
                "updated_at": "2026-10-02T12:00:00Z",
            }
        ],
        "allocation": {
            "allocated_fraction": "0.5",
            "cash_reserve_fraction": "0.2",
            "unallocated_fraction": "0.3",
            "allocated_quote": "500",
            "cash_reserve_quote": "200",
            "unallocated_quote": "300",
            "assets": [],
            "largest_asset": None,
            "largest_asset_within_limit": None,
        },
        "deployable": False,
    }


class Recorder:
    """A urlopen fake that serves JSON by ``METHOD path`` and records mutation bodies."""

    def __init__(self, handlers: dict[str, object]) -> None:
        """Serve these routes; anything else fails the test."""
        self.handlers = handlers
        self.bodies: dict[str, object] = {}

    def __call__(self, request: object, timeout: object = None) -> MagicMock:
        """Answer one request."""
        del timeout
        method = str(getattr(request, "method", "GET") or "GET").upper()
        url = str(getattr(request, "full_url", request))
        key = f"{method} {urlparse(url).path}"
        data = getattr(request, "data", None)
        if isinstance(data, bytes):
            self.bodies[key] = json.loads(data)
        if key not in self.handlers:
            raise AssertionError(f"unexpected agent HTTP request: {key}")
        return json_urlopen_response(self.handlers[key])


def _run(
    argv: list[str], handlers: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> tuple[int | str | None, str, Recorder]:
    """Run the CLI against the fake API."""
    recorder = Recorder({"GET /health/ready": matching_ready_payload(), **handlers})
    with (
        patch("thytrader.agent_http.urlopen", side_effect=recorder),
        pytest.raises(SystemExit) as raised,
    ):
        main(argv)
    return raised.value.code, capsys.readouterr().out, recorder


def test_mutations_require_confirm_before_any_request(capsys: pytest.CaptureFixture[str]) -> None:
    """Without --confirm nothing is sent; YOLO is not consulted."""
    code, _out, recorder = _run(
        ["create", "--name", "Core", "--mode", "paper", "--capital-quote", "1000"], {}, capsys
    )
    assert code != 0
    assert "Pass --confirm" in str(code)
    assert recorder.bodies == {}


def test_create_sends_the_validated_body(capsys: pytest.CaptureFixture[str]) -> None:
    """Create validates locally and POSTs canonical decimals."""
    code, out, recorder = _run(
        [
            "create",
            "--name",
            "Core",
            "--mode",
            "live",
            "--capital-quote",
            "300.50",
            "--cash-reserve-fraction",
            "0.17",
            "--confirm",
        ],
        {"POST /api/v1/portfolios": _portfolio()},
        capsys,
    )
    assert code == 0
    body = cast("dict[str, object]", recorder.bodies["POST /api/v1/portfolios"])
    assert (body["mode"], body["capital_quote"], body["cash_reserve_fraction"]) == (
        "live",
        "300.5",
        "0.17",
    )
    assert json.loads(out)["portfolio_id"] == _PID


def test_update_merges_limit_and_manager_flags(capsys: pytest.CaptureFixture[str]) -> None:
    """Flags change only what they name; the rest of limits/manager is kept."""
    code, _out, recorder = _run(
        [
            "update",
            "--portfolio-id",
            _PID,
            "--revision",
            "3",
            "--max-per-asset-fraction",
            "0.6",
            "--may-rebalance",
            "yes",
            "--confirm",
        ],
        {
            f"GET /api/v1/portfolios/{_PID}": _portfolio(),
            f"PATCH /api/v1/portfolios/{_PID}": _portfolio(),
        },
        capsys,
    )
    assert code == 0
    body = recorder.bodies[f"PATCH /api/v1/portfolios/{_PID}"]
    assert body == {
        "revision": 3,
        "limits": {"max_total_exposure_fraction": "1", "max_per_asset_fraction": "0.6"},
        "manager": {
            "mandate": "",
            "permissions": {
                "may_rebalance": True,
                "max_weight_change_per_week": "0.1",
                "may_pause_sleeves": False,
                "may_propose_sleeves": False,
            },
        },
    }


def test_set_weights_and_remove_resolve_strategy_ids(capsys: pytest.CaptureFixture[str]) -> None:
    """IDs may be strategy ids; the CLI maps them to sleeves."""
    handlers: dict[str, object] = {
        f"GET /api/v1/portfolios/{_PID}": _portfolio(),
        f"PUT /api/v1/portfolios/{_PID}/weights": _portfolio(),
        f"DELETE /api/v1/portfolios/{_PID}/sleeves/{_SLEEVE}": _portfolio(),
    }
    code, _out, recorder = _run(
        [
            "set-weights",
            "--portfolio-id",
            _PID,
            "--revision",
            "3",
            "--weight",
            f"{_STRATEGY}=0.6",
            "--confirm",
        ],
        handlers,
        capsys,
    )
    assert code == 0
    assert recorder.bodies[f"PUT /api/v1/portfolios/{_PID}/weights"] == {
        "revision": 3,
        "weights": [{"sleeve_id": _SLEEVE, "weight_fraction": "0.6"}],
    }
    code, _out, _recorder = _run(
        [
            "remove-sleeve",
            "--portfolio-id",
            _PID,
            "--revision",
            "3",
            "--strategy-id",
            _STRATEGY,
            "--confirm",
        ],
        handlers,
        capsys,
    )
    assert code == 0


def test_backtest_queues_and_show_backtest_returns_the_job(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Backtest POSTs the costs; show-backtest prints the job (and result once completed)."""
    job = {
        "job_id": "01a0f000-0000-7000-8000-000000000301",
        "portfolio_id": _PID,
        "portfolio_revision": 3,
        "status": "queued",
        "created_at": "2026-10-02T12:00:00Z",
        "updated_at": "2026-10-02T12:00:00Z",
        "expires_at": "2026-10-03T12:00:00Z",
        "evaluation_start": "2026-07-10T08:00:00Z",
        "evaluation_end": "2026-07-17T12:00:00Z",
        "sleeve_count": 1,
        "progress_current": 0,
        "progress_total": 2,
    }
    code, out, recorder = _run(
        [
            "backtest",
            "--portfolio-id",
            _PID,
            "--maker-fee-rate",
            "0.004",
            "--taker-fee-rate",
            "0.006",
            "--fixed-slippage-bps",
            "5",
            "--confirm",
        ],
        {f"POST /api/v1/portfolios/{_PID}/backtests": {"job": job, "sleeves": []}},
        capsys,
    )
    assert code == 0
    assert recorder.bodies[f"POST /api/v1/portfolios/{_PID}/backtests"] == {
        "maker_fee_rate": "0.004",
        "taker_fee_rate": "0.006",
        "fixed_slippage_bps": "5",
        "datasets": [],
    }
    assert json.loads(out)["job"]["status"] == "queued"
    code, out, _recorder = _run(
        ["show-backtest", "--portfolio-id", _PID, "--job-id", str(job["job_id"])],
        {f"GET /api/v1/portfolios/{_PID}/backtests/jobs/{job['job_id']}": job},
        capsys,
    )
    assert code == 0
    assert "result" not in json.loads(out)


def test_reads_fail_closed_on_a_stale_image(capsys: pytest.CaptureFixture[str]) -> None:
    """A missing ops contract stops the command before the portfolio request."""
    calls: list[str] = []

    def fake(request: object, timeout: object = None) -> MagicMock:
        """Serve a stale ready payload only."""
        del timeout
        url = str(getattr(request, "full_url", request))
        calls.append(url)
        return json_urlopen_response(stale_ready_payload())

    fake_urlopen: Callable[..., MagicMock] = fake
    with (
        patch("thytrader.agent_http.urlopen", side_effect=fake_urlopen),
        pytest.raises(SystemExit) as raised,
    ):
        main(["list"])
    assert "stale Compose image" in str(raised.value.code)
    assert all(url.endswith("/health/ready") for url in calls)
    capsys.readouterr()


def test_create_takes_limits_mandate_and_permissions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Create sets limits and manager settings in its one revision (ADR 0094)."""
    document = tmp_path / "portfolio.json"
    document.write_text(
        json.dumps({"name": "Majors", "mode": "paper", "capital_quote": "1000"}), "utf-8"
    )
    code, _out, recorder = _run(
        [
            "create",
            "--file",
            str(document),
            "--max-per-asset-fraction",
            "0.25",
            "--daily-loss-quote",
            "50",
            "--mandate",
            "Trend-follow the majors.",
            "--may-rebalance",
            "yes",
            "--confirm",
        ],
        {"POST /api/v1/portfolios": _portfolio()},
        capsys,
    )
    assert code == 0
    body = cast("dict[str, Any]", recorder.bodies["POST /api/v1/portfolios"])
    assert body["name"] == "Majors"
    assert body["limits"]["max_per_asset_fraction"] == "0.25"
    assert body["limits"]["daily_loss_quote"] == "50"
    assert body["manager"]["mandate"] == "Trend-follow the majors."
    assert body["manager"]["permissions"]["may_rebalance"] is True


def test_create_file_refuses_sleeves(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Sleeves go through add-sleeves --file (one revision), never a second create path."""
    document = tmp_path / "portfolio.json"
    document.write_text(
        json.dumps({"name": "M", "mode": "paper", "capital_quote": "1", "sleeves": []}), "utf-8"
    )
    code, _out, recorder = _run(["create", "--file", str(document), "--confirm"], {}, capsys)
    assert "add-sleeves --file" in str(code)
    assert "POST /api/v1/portfolios" not in recorder.bodies


def test_add_sleeves_posts_one_batch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """add-sleeves --file sends every sleeve to the batch route with the revision."""
    sleeves = tmp_path / "sleeves.json"
    second = "0199bbbb-bbbb-7bbb-bbbb-bbbbbbbbbbbb"
    sleeves.write_text(
        json.dumps(
            [
                {"strategy_id": _STRATEGY, "weight_fraction": "0.4"},
                {"strategy_id": second, "weight_fraction": "0.3", "note": "ETH"},
            ]
        ),
        "utf-8",
    )
    path = f"/api/v1/portfolios/{_PID}/sleeves/batch"
    code, _out, recorder = _run(
        [
            "add-sleeves",
            "--portfolio-id",
            _PID,
            "--revision",
            "3",
            "--file",
            str(sleeves),
            "--confirm",
        ],
        {f"POST {path}": _portfolio()},
        capsys,
    )
    assert code == 0
    body = cast("dict[str, Any]", recorder.bodies[f"POST {path}"])
    assert body["revision"] == 3
    assert [item["strategy_id"] for item in body["sleeves"]] == [_STRATEGY, second]


def test_delete_dry_run_previews_and_delete_sends_the_revision(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Delete --dry-run reads only; delete DELETEs with the revision guard."""
    code, out, recorder = _run(
        ["delete", "--portfolio-id", _PID, "--revision", "2", "--dry-run"],
        {f"GET /api/v1/portfolios/{_PID}": _portfolio()},
        capsys,
    )
    assert code == 0
    preview = json.loads(out)
    assert preview["dry_run"] is True
    assert preview["sleeves"] == 1
    assert recorder.bodies == {}
    deletion = {
        "portfolio_id": _PID,
        "name": "Core",
        "sleeves": 1,
        "journal_entries": 3,
        "backtests": 0,
        "backtest_jobs": 0,
    }
    code, out, _recorder = _run(
        ["delete", "--portfolio-id", _PID, "--revision", "2", "--confirm"],
        {f"DELETE /api/v1/portfolios/{_PID}": deletion},
        capsys,
    )
    assert code == 0
    assert json.loads(out)["journal_entries"] == 3


def test_delete_requires_confirm(capsys: pytest.CaptureFixture[str]) -> None:
    """A real delete is a mutation."""
    code, _out, recorder = _run(["delete", "--portfolio-id", _PID, "--revision", "2"], {}, capsys)
    assert "Pass --confirm" in str(code)
    assert recorder.bodies == {}


def test_sleeve_help_states_the_cap(capsys: pytest.CaptureFixture[str]) -> None:
    """create, add-sleeve, and add-sleeves --help name the 32-sleeve cap."""
    for command in ("create", "add-sleeve", "add-sleeves"):
        with pytest.raises(SystemExit):
            main([command, "--help"])
        assert "at most 32 sleeves" in " ".join(capsys.readouterr().out.split())
