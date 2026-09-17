"""Mutation lane HTTP clients send installation auth when the boundary is on."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

from thytrader.agent_http import request_mutation_json
from thytrader.agent_orchestration.client import record_skipped_confirmation
from thytrader.agent_orchestration.models import YoloTier
from thytrader.config import Environment, Settings
from thytrader.data_control.client import add_watch, fill_gaps
from thytrader.memory.client import add_journal
from thytrader.research.http import create_draft
from thytrader.security.models import INSTALLATION_AUTH_HEADER


def _boundary_settings() -> Settings:
    """Return test settings with trust-boundary auth enabled."""
    return Settings(
        environment=Environment.TEST,
        installation_token=SecretStr("lane-mutation-token"),
        trust_boundary_enabled=True,
        _env_file=None,
    )


def _capture_auth_header() -> tuple[Any, dict[str, str]]:
    """Patch urlopen and return the captured request headers."""
    captured: dict[str, str] = {}

    def fake_urlopen(request: object, timeout: object = None) -> MagicMock:
        del timeout
        header_items = getattr(request, "header_items", None)
        if callable(header_items):
            captured.update(dict(header_items()))
        response = MagicMock()
        response.status = 200
        response.read.return_value = b"{}"
        response.__enter__.return_value = response
        response.__exit__.return_value = None
        return response

    return patch("thytrader.agent_http.urlopen", side_effect=fake_urlopen), captured


def test_request_mutation_json_sends_installation_bearer() -> None:
    """Shared helper attaches Authorization when trust boundary is enabled."""
    patcher, captured = _capture_auth_header()
    settings = _boundary_settings()
    with patcher:
        request_mutation_json(
            method="POST",
            url="http://127.0.0.1:8200/api/v1/example",
            payload={"ok": True},
            settings=settings,
        )
    assert captured.get(INSTALLATION_AUTH_HEADER) == "Bearer lane-mutation-token"


def test_data_client_watch_add_sends_installation_bearer() -> None:
    """Data mutations use the shared installation-auth helper."""
    patcher, captured = _capture_auth_header()
    settings = _boundary_settings()
    with (
        patcher,
        patch(
            "thytrader.agent_http.Settings",
            return_value=settings,
        ),
    ):
        add_watch(
            "http://127.0.0.1:8200",
            product_id="ETH-USD",
            timeframe="5m",
            lookback_hours=168,
            enabled=True,
        )
    assert captured.get(INSTALLATION_AUTH_HEADER) == "Bearer lane-mutation-token"


def test_data_client_fill_gaps_sends_installation_bearer() -> None:
    """Fill-gaps continuation POSTs use the shared installation-auth helper."""
    patcher, captured = _capture_auth_header()
    settings = _boundary_settings()
    with (
        patcher,
        patch(
            "thytrader.agent_http.Settings",
            return_value=settings,
        ),
        patch(
            "thytrader.data_control.client.ingest_status",
            return_value={"ingest_requested_at": None},
        ),
    ):
        fill_gaps(
            "http://127.0.0.1:8200",
            product_id="DOGE-USD",
            timeframe="5m",
        )
    assert captured.get(INSTALLATION_AUTH_HEADER) == "Bearer lane-mutation-token"


def test_memory_client_add_journal_sends_installation_bearer() -> None:
    """Memory mutations use the shared installation-auth helper."""
    patcher, captured = _capture_auth_header()
    settings = _boundary_settings()
    with (
        patcher,
        patch(
            "thytrader.agent_http.Settings",
            return_value=settings,
        ),
    ):
        add_journal(
            "http://127.0.0.1:8200",
            {
                "origin": "agent",
                "kind": "observation",
                "title": "t",
                "body": "b",
                "evidence_kind": "backtest_result",
                "evidence_id": "fp",
            },
        )
    assert captured.get(INSTALLATION_AUTH_HEADER) == "Bearer lane-mutation-token"


def test_research_client_create_draft_sends_installation_bearer() -> None:
    """Research mutations use the shared installation-auth helper."""
    patcher, captured = _capture_auth_header()
    settings = _boundary_settings()
    with (
        patcher,
        patch(
            "thytrader.agent_http.Settings",
            return_value=settings,
        ),
        patch(
            "thytrader.research.http._as_object",
            side_effect=lambda value, _: value if isinstance(value, dict) else {},
        ),
        patch(
            "thytrader.research.http._as_str",
            return_value="00000000-0000-0000-0000-000000000001",
        ),
        patch(
            "thytrader.research.http._encode",
            return_value="{}",
        ),
    ):
        create_draft("http://127.0.0.1:8200")
    assert captured.get(INSTALLATION_AUTH_HEADER) == "Bearer lane-mutation-token"


def test_orchestration_skip_audit_sends_installation_bearer() -> None:
    """YOLO skip audits are HTTP mutations and require installation auth."""
    patcher, captured = _capture_auth_header()
    settings = _boundary_settings()
    with (
        patcher,
        patch(
            "thytrader.agent_http.Settings",
            return_value=settings,
        ),
        patch(
            "thytrader.agent_orchestration.client.SkippedConfirmationResponse.model_validate",
            return_value=MagicMock(),
        ),
    ):
        record_skipped_confirmation(
            "http://127.0.0.1:8200",
            tier=YoloTier.DATA,
            command="watch-add",
        )
    assert captured.get(INSTALLATION_AUTH_HEADER) == "Bearer lane-mutation-token"
