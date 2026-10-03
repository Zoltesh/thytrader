"""Explicit comparison identity, conflicts, restart-independent saves, and HTTP safety."""

from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from tests.execution.test_fill_comparison import _deployment
from thytrader.api.app import create_app
from thytrader.config import Environment, Settings
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import DeploymentKind, DeploymentMode, DeploymentStatus
from thytrader.execution.twins import TwinConflictError, TwinValidationError, comparable_twins
from thytrader.persistence.audit_events import InMemoryAuditEventStore
from thytrader.security.models import INSTALLATION_AUTH_HEADER


@pytest.mark.anyio
async def test_pair_is_idempotent_one_to_one_and_survives_worker_save() -> None:
    """Pair selection never rewrites cash, lifecycle, revisions, leases, or orders."""
    store = InMemoryExecutionStore()
    paper = await store.create_deployment(_deployment(DeploymentMode.PAPER))
    live = await store.create_deployment(_deployment(DeploymentMode.LIVE))
    other = await store.create_deployment(_deployment(DeploymentMode.LIVE))
    link = await store.link_twins(paper.id, live.id)
    assert await store.link_twins(live.id, paper.id) == link
    with pytest.raises(TwinConflictError):
        await store.link_twins(paper.id, other.id)
    assert store.deployments[paper.id] == paper
    assert store.deployments[live.id] == live
    assert not store.orders and not store.intents
    await store.save_deployment(replace(paper, status=DeploymentStatus.PAUSED))
    assert await store.get_twin_link(paper.id) == link
    await store.unlink_twins(live.id, paper.id)
    await store.unlink_twins(paper.id, live.id)
    await store.link_twins(paper.id, other.id)
    with pytest.raises(TwinConflictError):
        await store.unlink_twins(paper.id, live.id)
    assert (await store.list_twin_links())[0].live_deployment_id == other.id


@pytest.mark.parametrize("invalid", ["same_mode", "discretionary", "snapshot", "market", "clock"])
def test_comparison_validation_rejects_incomparable_books(invalid: str) -> None:
    """Different semantics cannot be presented as a controlled paper/live comparison."""
    paper = _deployment(DeploymentMode.PAPER)
    live = _deployment(DeploymentMode.LIVE)
    match invalid:
        case "same_mode":
            live = replace(live, mode=DeploymentMode.PAPER)
        case "discretionary":
            live = replace(live, kind=DeploymentKind.DISCRETIONARY)
        case "snapshot":
            live = replace(live, strategy_fingerprint="sha256:" + "d" * 64)
        case "market":
            live = replace(live, product_id="BTC-USD")
        case "clock":
            live = replace(live, timeframe="5m")
        case _:
            raise AssertionError(invalid)
    with pytest.raises(TwinValidationError):
        comparable_twins(paper, live)


def test_http_pair_controls_are_metadata_only_audited_and_guarded() -> None:
    """Both directions read the same pair; stale unlinks preserve a replacement."""
    store = InMemoryExecutionStore()
    audit = InMemoryAuditEventStore()
    paper = _deployment(DeploymentMode.PAPER)
    live = _deployment(DeploymentMode.LIVE)
    other = _deployment(DeploymentMode.LIVE)
    store.deployments = {bot.id: bot for bot in (paper, live, other)}
    with TestClient(
        create_app(Settings(_env_file=None), execution_store=store, audit_event_store=audit)
    ) as client:
        path = f"/api/v1/deployments/{paper.id}/twin"
        assert client.get(path).json()["twin"] is None
        body = {"counterpart_deployment_id": str(live.id)}
        response = client.put(path, json=body)
        assert response.status_code == 200
        pair = response.json()["twin"]
        assert pair["paper_deployment_id"] == str(paper.id)
        assert pair["live_deployment_id"] == str(live.id)
        assert client.get(f"/api/v1/deployments/{live.id}/twin").json()["twin"] == pair
        assert client.put(path, json=body).json()["twin"] == pair
        assert (
            client.put(path, json={"counterpart_deployment_id": str(other.id)}).status_code == 409
        )
        assert client.put(path, json={"counterpart_deployment_id": str(uuid4())}).status_code == 404
        assert (
            client.put(path, json={"counterpart_deployment_id": str(paper.id)}).status_code == 422
        )
        assert client.put(path, json={**body, "i_understand_live": True}).status_code == 422
        assert client.delete(path).status_code == 422
        assert client.delete(path, params=body).status_code == 200
        assert (
            client.put(path, json={"counterpart_deployment_id": str(other.id)}).status_code == 200
        )
        assert client.delete(path, params=body).status_code == 409
        assert client.get(path).json()["twin"]["live_deployment_id"] == str(other.id)
    assert store.deployments[paper.id] == paper and store.deployments[live.id] == live
    assert not store.orders and not store.intents
    assert {event.action for event in audit._events} == {
        "link_deployment_twins",
        "unlink_deployment_twins",
    }


def test_browser_mutation_requires_the_existing_csrf_boundary() -> None:
    """Twin metadata obeys the same trusted-origin and CSRF rules as other mutations."""
    store = InMemoryExecutionStore()
    paper, live = _deployment(DeploymentMode.PAPER), _deployment(DeploymentMode.LIVE)
    store.deployments = {paper.id: paper, live.id: live}
    with TestClient(
        create_app(
            Settings(
                _env_file=None,
                environment=Environment.TEST,
                trust_boundary_enabled=True,
                installation_token=SecretStr("twin-test-token"),
            ),
            execution_store=store,
        ),
        base_url="http://127.0.0.1:8000",
    ) as client:
        response = client.put(
            f"/api/v1/deployments/{paper.id}/twin",
            json={"counterpart_deployment_id": str(live.id)},
            headers={
                "Origin": "http://127.0.0.1:8000",
                INSTALLATION_AUTH_HEADER: "Bearer twin-test-token",
            },
        )
    assert response.status_code == 401
    assert "CSRF" in response.json()["detail"]
    assert not store.twin_links
