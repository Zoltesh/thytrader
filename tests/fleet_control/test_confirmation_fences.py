"""Public fleet latch revision confirmation and idempotency fingerprint gates."""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.runtime_control.cli import _parser
from thytrader.runtime_control.client import RuntimeControlError
from thytrader.runtime_control.fleet_commands import run_fleet_mutation
from thytrader.trading.memory import InMemoryExecutionStore


def test_http_latch_requires_exact_preview_revisions_and_both_live_gates() -> None:
    """Confirmation/ack cannot bypass stale or absent revision fences."""
    app = create_app(Settings(_env_file=None), execution_store=InMemoryExecutionStore())
    with TestClient(app) as client:
        payload = {
            "mode": "live",
            "confirm": True,
            "idempotency_key": "original",
            "expected_inhibition": {"live_revision": 0},
        }
        assert client.post("/api/v1/fleet-control/rearm", json=payload).status_code == 428
        assert (
            client.post(
                "/api/v1/fleet-control/rearm",
                json={**payload, "i_understand_live": True, "confirm": False},
            ).status_code
            == 409
        )
        accepted = client.post(
            "/api/v1/fleet-control/rearm", json={**payload, "i_understand_live": True}
        )
        assert accepted.status_code == 200
        assert accepted.json()["inhibition"]["live_revision"] == 1
        stale = client.post(
            "/api/v1/fleet-control/disarm", json={**payload, "idempotency_key": "stale"}
        )
        assert stale.status_code == 409
        assert "inhibition_revision_conflict" in stale.json()["detail"]
        changed_retry = client.post(
            "/api/v1/fleet-control/rearm",
            json={
                **payload,
                "i_understand_live": True,
                "expected_inhibition": {"live_revision": 1},
            },
        )
        assert changed_retry.status_code == 409
        assert "different fleet request" in changed_retry.json()["detail"]
        missing = client.post(
            "/api/v1/fleet-control/disarm",
            json={"mode": "paper", "confirm": True, "idempotency_key": "missing"},
        )
        assert missing.status_code == 409
        assert "expected_inhibition_required" in missing.json()["detail"]
        invalid = client.post(
            "/api/v1/fleet-control/disarm",
            json={**payload, "expected_inhibition": {"live_revision": True}},
        )
        assert invalid.status_code == 422


def test_cli_requires_latch_revision_confirmation_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not replace the operator's stale preview with a fresh automatic read."""
    calls: list[object] = []

    def preflight(base_url: str) -> None:
        """Record any forbidden automatic probe before explicit confirmation."""
        calls.append(base_url)

    monkeypatch.setattr(
        "thytrader.runtime_control.fleet_commands.require_matching_ops_contract", preflight
    )
    arguments = _parser().parse_args(
        [
            "fleet-disarm",
            "--mode",
            "all",
            "--idempotency-key",
            "key",
            "--confirm",
            "--expect-inhibition",
            "paper:2",
        ]
    )
    with pytest.raises(RuntimeControlError, match="expect-inhibition"):
        run_fleet_mutation(arguments, "http://127.0.0.1:8200", Settings(_env_file=None))
    assert calls == []
