"""HTTP and CLI gates for inventory honesty and fleet controls."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from thytrader.api.app import create_app
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.config import Environment, Settings
from thytrader.execution.entry_latch import clear_entry_inhibition_cache
from thytrader.execution.ids import uuid7
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    ExecutionConflictError,
    RuntimePhase,
)
from thytrader.runtime_control.cli import _parser
from thytrader.runtime_control.client import RuntimeControlError
from thytrader.runtime_control.fleet_commands import run_fleet_mutation
from thytrader.runtime_control.inventory_commands import run_inventory_read
from thytrader.security.models import CSRF_COOKIE, CSRF_HEADER, INSTALLATION_AUTH_HEADER


def _book() -> Deployment:
    """One paper discretionary book."""
    created = datetime(2026, 10, 6, tzinfo=UTC)
    return Deployment(
        id=uuid7(created),
        strategy_fingerprint=None,
        strategy_id=None,
        product_id="BTC-USDC",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("1"),
        phase=RuntimePhase.FLAT,
        created_at=created,
        updated_at=created,
        kind=DeploymentKind.DISCRETIONARY,
        timeframe="5m",
        paper_maker_fee_rate=Decimal("0.001"),
        paper_taker_fee_rate=Decimal("0.002"),
    )


def test_http_disarm_requires_confirm_and_blocks_a_later_start() -> None:
    """Disarm is not flatten, and a restarted admission check still refuses."""
    execution = InMemoryExecutionStore()
    book = _book()
    execution.deployments[book.id] = book
    app = create_app(
        Settings(_env_file=None),
        execution_store=execution,
        audit_event_store=InMemoryAuditEventStore(),
    )
    payload = {
        "mode": "paper",
        "confirm": True,
        "idempotency_key": "disarm-1",
        "expected_inhibition": {"paper_revision": 0},
        "allow_empty_scope": True,
    }
    with TestClient(app) as client:
        missing = client.post("/api/v1/fleet-control/disarm", json={**payload, "confirm": False})
        assert missing.status_code == 409
        accepted = client.post("/api/v1/fleet-control/disarm", json=payload)
        assert accepted.status_code == 200
        body = accepted.json()
        assert body["action"] == "disarm"
        assert body["atomic_venue_transaction"] is False
        assert "not" in body["note"] or "Latch updated" in body["note"]
        preview = client.get(
            "/api/v1/fleet-control/preview", params={"action": "disarm", "mode": "paper"}
        )
        assert preview.status_code == 200
        assert preview.json()["flattens"] is False
        assert preview.json()["pauses"] is False
        show = client.get(f"/api/v1/deployments/{book.id}")
        assert show.status_code == 200
        assert show.json()["ledger_omission"]
        assert show.json()["orders"] == []
        assert show.json()["historical_orders_included"] is False
    assert execution.deployments[book.id].status is DeploymentStatus.RUNNING
    with pytest.raises(ExecutionConflictError, match="ENTRY_INHIBITED"):
        asyncio.run(execution.create_deployment(_book()))
    clear_entry_inhibition_cache()


def test_fleet_browser_mutation_still_requires_csrf() -> None:
    """The existing trust boundary still covers the new fleet routes."""
    settings = Settings(
        environment=Environment.TEST,
        installation_token=SecretStr("fleet-token"),
        trust_boundary_enabled=True,
        _env_file=None,
    )
    app = create_app(settings, execution_store=InMemoryExecutionStore())
    payload = {
        "mode": "paper",
        "confirm": True,
        "idempotency_key": "disarm-csrf",
        "expected_inhibition": {"paper_revision": 0},
        "allow_empty_scope": True,
    }
    with TestClient(app) as client:
        denied = client.post("/api/v1/fleet-control/disarm", json=payload)
        assert denied.status_code == 401
        session = client.get(
            "/api/v1/security/session",
            headers={INSTALLATION_AUTH_HEADER: "Bearer fleet-token"},
        )
        assert session.status_code == 200
        token = session.json()["csrf_token"]
        blocked = client.post(
            "/api/v1/fleet-control/disarm",
            json=payload,
            headers={
                INSTALLATION_AUTH_HEADER: "Bearer fleet-token",
                "origin": "http://127.0.0.1:5173",
            },
        )
        assert blocked.status_code == 401
        allowed = client.post(
            "/api/v1/fleet-control/disarm",
            json=payload,
            headers={
                INSTALLATION_AUTH_HEADER: "Bearer fleet-token",
                "origin": "http://127.0.0.1:5173",
                CSRF_HEADER: token,
            },
            cookies={CSRF_COOKIE: token},
        )
        assert allowed.status_code == 200


def test_cli_page_does_not_claim_a_complete_fleet(monkeypatch: pytest.MonkeyPatch) -> None:
    """A limited list keeps has_more instead of walking the snapshot."""
    captured: dict[str, object] = {}

    def fake_list(
        base_url: str, *, limit: int | None = None, offset: int = 0, page_size: int = 200
    ) -> object:
        captured.update(limit=limit, offset=offset, page_size=page_size, base_url=base_url)
        return {
            "deployments": [],
            "returned": 0,
            "has_more": True,
            "as_of": "2026-10-06T00:00:00+00:00",
        }

    monkeypatch.setattr("thytrader.runtime_control.inventory_commands.list_deployments", fake_list)
    arguments = _parser().parse_args(["list", "--limit", "10", "--offset", "20"])
    payload = run_inventory_read(arguments, "http://127.0.0.1:8200")
    assert captured["limit"] == 10
    assert captured["offset"] == 20
    assert isinstance(payload, dict)
    page = {str(key): value for key, value in payload.items()}
    assert page["complete"] is False
    assert page["inventory"] == "page"


def test_cli_fleet_stop_requires_expect_and_confirm() -> None:
    """Managed stop is not sent until the operator confirms preview revisions."""
    parser = _parser()
    missing = parser.parse_args(
        ["fleet-stop", "--mode", "paper", "--idempotency-key", "k", "--confirm"]
    )
    with pytest.raises(RuntimeControlError, match="--expect"):
        run_fleet_mutation(missing, "http://127.0.0.1:8200", Settings(_env_file=None))
    unconfirmed = parser.parse_args(["fleet-disarm", "--mode", "paper", "--idempotency-key", "k"])
    with pytest.raises(RuntimeControlError, match="--confirm"):
        run_fleet_mutation(unconfirmed, "http://127.0.0.1:8200", Settings(_env_file=None))
