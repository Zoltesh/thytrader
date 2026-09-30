"""thytrader-runtime forwards --i-understand-live as the HTTP i_understand_live field."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from tests.http_fakes import matching_ready_payload, urlopen_by_path
from thytrader.runtime_control.cli import main

_DEPLOYMENT = "11111111-1111-1111-1111-111111111111"
_FINGERPRINT = "sha256:" + "a" * 64


def _run(argv: list[str], *, mode: str = "live") -> list[dict[str, Any]]:
    """Run the CLI with a matching API and capture each mutation request."""
    captured: list[dict[str, Any]] = []

    def fake_mutation(**kwargs: Any) -> object:
        captured.append(kwargs)
        return {"id": _DEPLOYMENT, "mode": mode, "status": "running"}

    handlers = {"GET /health/ready": matching_ready_payload()}
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.runtime_control.client.request_mutation_json", fake_mutation),
        patch(
            "thytrader.runtime_control.cli.show_deployment",
            return_value={"id": _DEPLOYMENT, "mode": mode},
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main(argv)
    assert raised.value.code == 0, raised.value
    return captured


def test_live_start_sends_explicit_acknowledgement() -> None:
    """Live start carries i_understand_live=true in the HTTP body."""
    captured = _run(
        [
            "start",
            "--strategy-id",
            "01985cf0-7b60-7000-8000-00000000c0de",
            "--mode",
            "live",
            "--confirm",
            "--i-understand-live",
        ]
    )
    assert captured[0]["payload"]["i_understand_live"] is True


def test_paper_start_does_not_send_live_acknowledgement() -> None:
    """Paper bodies never claim a live acknowledgement."""
    captured = _run(
        [
            "start",
            "--strategy-id",
            "01985cf0-7b60-7000-8000-00000000c0de",
            "--mode",
            "paper",
            "--cash",
            "100",
            "--confirm",
        ],
        mode="paper",
    )
    assert "i_understand_live" not in captured[0]["payload"]


def test_live_place_order_sends_explicit_acknowledgement() -> None:
    """Live place-order carries i_understand_live=true in the HTTP body."""
    captured = _run(
        [
            "place-order",
            "--mode",
            "live",
            "--product-id",
            "BTC-USDC",
            "--timeframe",
            "1h",
            "--entry-kind",
            "marketable",
            "--quantity",
            "0.0001",
            "--stop-price",
            "90000",
            "--take-profit-price",
            "120000",
            "--idempotency-key",
            "KEY",
            "--confirm",
            "--i-understand-live",
        ]
    )
    assert captured[0]["payload"]["i_understand_live"] is True


def test_live_resume_requires_flag_before_any_mutation() -> None:
    """Resuming a live deployment without --i-understand-live never POSTs."""
    handlers = {"GET /health/ready": matching_ready_payload()}
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch(
            "thytrader.runtime_control.cli.show_deployment",
            return_value={"id": _DEPLOYMENT, "mode": "live"},
        ),
        patch("thytrader.runtime_control.cli.set_deployment_status") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["resume", _DEPLOYMENT, "--confirm"])
    assert raised.value.code != 0
    assert "--i-understand-live" in str(raised.value)
    request.assert_not_called()


def test_live_resume_with_flag_sends_acknowledgement_body() -> None:
    """Live resume posts {"i_understand_live": true}."""
    captured = _run(["resume", _DEPLOYMENT, "--confirm", "--i-understand-live"])
    assert captured[0]["payload"] == {"i_understand_live": True}
    assert captured[0]["url"].endswith(f"/{_DEPLOYMENT}/resume")


def test_paper_resume_needs_no_live_flag() -> None:
    """Paper resume is unchanged and sends no body."""
    captured = _run(["resume", _DEPLOYMENT, "--confirm"], mode="paper")
    assert captured[0]["payload"] is None
