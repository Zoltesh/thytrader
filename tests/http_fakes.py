"""Shared urllib doubles for agent CLI stale-image tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol
from unittest.mock import MagicMock
from urllib.parse import urlparse

from thytrader.ops_contract import expected_ops_contract

if TYPE_CHECKING:
    from collections.abc import Mapping


class _HasFullUrl(Protocol):
    """urllib Request-shaped object used by the patched urlopen helper."""

    full_url: str


def matching_ready_payload() -> dict[str, object]:
    """Return a `/health/ready` body that matches this checkout's ops contract."""
    return {
        "status": "ready",
        "version": "0.1.0",
        "ops_contract": expected_ops_contract(),
    }


def stale_ready_payload() -> dict[str, object]:
    """Return a healthy ready body that omits the ops contract (pre-0019 image)."""
    return {"status": "ready", "version": "0.1.0"}


def json_urlopen_response(payload: object, *, status: int = 200) -> MagicMock:
    """Build a context-managed urlopen response that returns JSON bytes."""
    raw = json.dumps(payload).encode("utf-8")
    response = MagicMock()
    response.status = status
    response.read.return_value = raw
    response.__enter__.return_value = response
    response.__exit__.return_value = None
    return response


def urlopen_ready_then(
    ready_payload: dict[str, object],
    command_payload: object | None = None,
) -> object:
    """Return a urlopen side_effect that serves ready, then one command JSON body.

    Any other URL raises AssertionError so tests fail if a command runs when it
    should not.
    """

    def fake_urlopen(request: _HasFullUrl | str, timeout: object = None) -> MagicMock:
        del timeout
        requested_url = request if isinstance(request, str) else request.full_url
        if requested_url.endswith("/health/ready"):
            return json_urlopen_response(ready_payload)
        if command_payload is None:
            message = f"unexpected agent HTTP request: {requested_url}"
            raise AssertionError(message)
        return json_urlopen_response(command_payload)

    return fake_urlopen


def orchestration_status_payload(
    *,
    yolo_enabled: bool = False,
    yolo_tiers: tuple[str, ...] = (),
) -> dict[str, object]:
    """Return the GET /api/v1/agent-orchestration body."""
    return {
        "schema_version": "thytrader-agent-orchestration-v1",
        "confirmation_mode": "yolo" if yolo_enabled else "safe",
        "yolo_enabled": yolo_enabled,
        "yolo_tiers": list(yolo_tiers),
        "live_hard_gate": True,
        "live_authority": False,
        "playbook_sequence": ["data_healthy", "draft_publish", "backtest", "optional_paper"],
    }


def urlopen_by_path(handlers: Mapping[str, object]) -> object:
    """Serve JSON by ``METHOD path`` keys such as ``GET /health/ready``.

    Values are JSON-serializable payloads. Unknown routes raise AssertionError.
    """

    def fake_urlopen(request: _HasFullUrl | str, timeout: object = None) -> MagicMock:
        del timeout
        if isinstance(request, str):
            method = "GET"
            requested_url = request
        else:
            method = str(getattr(request, "method", "GET") or "GET").upper()
            requested_url = request.full_url
        path = urlparse(requested_url).path
        key = f"{method} {path}"
        if key not in handlers:
            message = f"unexpected agent HTTP request: {key}"
            raise AssertionError(message)
        return json_urlopen_response(handlers[key])

    return fake_urlopen
