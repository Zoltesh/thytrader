"""The manager-loop commands of the thytrader-portfolio CLI (ADR 0091)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from tests.portfolios.test_cli import _PID, _SLEEVE, _STRATEGY, _portfolio, _run

if TYPE_CHECKING:
    import pytest

_PROPOSAL = "01a0f000-0000-7000-8000-000000000301"
_FINGERPRINT = "sha256:" + "a" * 64


def _proposal(status: str = "pending") -> dict[str, object]:
    """One ProposalResponse body."""
    return {
        "proposal": {
            "proposal_id": _PROPOSAL,
            "portfolio_id": _PID,
            "kind": "pause_sleeve",
            "status": status,
            "summary": "Pause sleeve “EMA”.",
            "rationale": "Drawdown is 1.6x its backtest.",
            "change": {"kind": "pause_sleeve", "sleeve_id": _SLEEVE},
            "evidence": [],
            "base_revision": 3,
            "submitted_by": "manager",
            "channel": "api",
            "created_at": "2026-10-02T12:00:00Z",
            "expires_at": "2026-10-09T12:00:00Z",
        },
        "portfolio_revision": 3,
    }


def test_propose_resolves_strategy_ids_and_sends_rationale_and_evidence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A rebalance names every sleeve (by strategy id here) with its evidence."""
    code, out, recorder = _run(
        [
            "propose",
            "--portfolio-id",
            _PID,
            "--revision",
            "3",
            "--kind",
            "rebalance",
            "--weight",
            f"{_STRATEGY}=0.45",
            "--rationale",
            "Trim by its walk-forward miss.",
            "--evidence",
            f"backtest_result={_FINGERPRINT}",
            "--evidence",
            "decision=01a0f000-0000-7000-8000-000000000401/BTC-USDC@2026-10-02T11:00:00Z",
            "--confirm",
        ],
        {
            f"GET /api/v1/portfolios/{_PID}": _portfolio(),
            f"POST /api/v1/portfolios/{_PID}/proposals": _proposal(),
        },
        capsys,
    )
    assert code == 0, code
    body = cast("dict[str, object]", recorder.bodies[f"POST /api/v1/portfolios/{_PID}/proposals"])
    assert body["revision"] == 3
    assert body["change"] == {
        "kind": "rebalance",
        "weights": [{"sleeve_id": _SLEEVE, "weight_fraction": "0.45"}],
    }
    assert body["rationale"] == "Trim by its walk-forward miss."
    assert [item["kind"] for item in cast("list[dict[str, str]]", body["evidence"])] == [
        "backtest_result",
        "decision",
    ]
    assert json.loads(out)["proposal"]["status"] == "pending"


def test_propose_needs_confirm_and_never_offers_an_order_kind(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutations need --confirm; an order kind is not a choice at all."""
    code, _out, recorder = _run(
        [
            "propose",
            "--portfolio-id",
            _PID,
            "--revision",
            "3",
            "--kind",
            "pause-sleeve",
            "--sleeve-id",
            _SLEEVE,
            "--rationale",
            "x",
        ],
        {},
        capsys,
    )
    assert "Pass --confirm" in str(code)
    assert recorder.bodies == {}
    usage, _out, refused = _run(
        ["propose", "--portfolio-id", _PID, "--revision", "3", "--kind", "place_order"],
        {},
        capsys,
    )
    assert usage == 2
    assert refused.bodies == {}


def test_approve_sends_the_note_and_the_live_acknowledgement(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Approve is a person's decision: --confirm, an optional note, and the live flag."""
    route = f"POST /api/v1/portfolios/{_PID}/proposals/{_PROPOSAL}/approve"
    code, out, recorder = _run(
        [
            "approve",
            "--portfolio-id",
            _PID,
            "--proposal-id",
            _PROPOSAL,
            "--note",
            "Agreed.",
            "--i-understand-live",
            "--confirm",
        ],
        {route: _proposal("applied")},
        capsys,
    )
    assert code == 0, code
    assert recorder.bodies[route] == {"note": "Agreed.", "i_understand_live": True}
    assert json.loads(out)["proposal"]["status"] == "applied"


def test_reads_need_no_confirm(capsys: pytest.CaptureFixture[str]) -> None:
    """deployment, briefing, proposals, and show-proposal are read-only."""
    briefing = {"contract": "thytrader-portfolio-briefing-v1"}
    code, out, _recorder = _run(
        ["briefing", "--portfolio-id", _PID, "--decisions-per-sleeve", "3"],
        {f"GET /api/v1/portfolios/{_PID}/briefing": briefing},
        capsys,
    )
    assert code == 0
    assert json.loads(out) == briefing
    listing = {"proposals": [], "limit": 20, "returned": 0, "total": 0, "has_more": False}
    code, out, _recorder = _run(
        ["proposals", "--portfolio-id", _PID, "--status", "pending"],
        {f"GET /api/v1/portfolios/{_PID}/proposals": listing},
        capsys,
    )
    assert code == 0
    assert json.loads(out)["total"] == 0
