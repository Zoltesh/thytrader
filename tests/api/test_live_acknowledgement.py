"""Live HTTP mutations require an explicit ``i_understand_live`` acknowledgement."""

from __future__ import annotations

import asyncio

from tests.api.test_deployments import (
    InMemoryPublicationStore,
    _client,
    _published_risk_policy_store,
    _published_strategy,
)
from tests.api.test_discretionary_orders import _body, _client as _order_client
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.strategies.models import strategy_fingerprint
from thytrader.strategies.publication import PublishedStrategy


def _stores() -> tuple[InMemoryPublicationStore, InMemoryExecutionStore, str]:
    """Return publication and execution stores holding one published strategy."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )
    return publication, execution, fingerprint


def test_live_start_without_acknowledgement_is_428_and_creates_nothing() -> None:
    """Missing or false acknowledgement fails closed; a non-boolean is a 422."""
    publication, execution, fingerprint = _stores()
    with _client(
        publication, execution, live_credentials=True, risk=_published_risk_policy_store()
    ) as client:
        missing = client.post(
            "/api/v1/deployments",
            json={"strategy_fingerprint": fingerprint, "mode": "live"},
        )
        false_ack = client.post(
            "/api/v1/deployments",
            json={"strategy_fingerprint": fingerprint, "mode": "live", "i_understand_live": False},
        )
        string_ack = client.post(
            "/api/v1/deployments",
            json={"strategy_fingerprint": fingerprint, "mode": "live", "i_understand_live": "yes"},
        )
    assert missing.status_code == 428
    assert missing.json()["detail"].startswith("live_acknowledgement_required:")
    assert false_ack.status_code == 428
    assert string_ack.status_code == 422
    assert asyncio.run(execution.list_deployments()) == ()


def test_paper_start_needs_no_live_acknowledgement() -> None:
    """Paper is unaffected by the live acknowledgement."""
    publication, execution, fingerprint = _stores()
    with _client(publication, execution) as client:
        response = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
                "mode": "paper",
                "paper_starting_cash": "1000",
            },
        )
    assert response.status_code == 201


def test_live_resume_requires_acknowledgement_but_paper_resume_does_not() -> None:
    """Resuming a live book re-arms orders; the book stays paused without the ack."""
    publication, execution, fingerprint = _stores()
    with _client(
        publication, execution, live_credentials=True, risk=_published_risk_policy_store()
    ) as client:
        created = client.post(
            "/api/v1/deployments",
            json={"strategy_fingerprint": fingerprint, "mode": "live", "i_understand_live": True},
        )
        live_id = created.json()["id"]
        client.post(f"/api/v1/deployments/{live_id}/pause")
        refused = client.post(f"/api/v1/deployments/{live_id}/resume")
        still_paused = client.get(f"/api/v1/deployments/{live_id}")
        resumed = client.post(
            f"/api/v1/deployments/{live_id}/resume", json={"i_understand_live": True}
        )
        paper = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
                "mode": "paper",
                "paper_starting_cash": "1000",
            },
        )
        paper_id = paper.json()["id"]
        client.post(f"/api/v1/deployments/{paper_id}/pause")
        paper_resumed = client.post(f"/api/v1/deployments/{paper_id}/resume")
    assert refused.status_code == 428
    assert still_paused.json()["status"] == "paused"
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "running"
    assert paper_resumed.status_code == 200


def test_live_place_order_without_acknowledgement_is_428() -> None:
    """A live discretionary ticket without the ack never reaches risk or the broker."""
    execution = InMemoryExecutionStore()
    with _order_client(execution=execution, live_credentials=True) as client:
        payload = _body(mode="live")
        payload.pop("paper_starting_cash")
        payload.pop("i_understand_live")
        response = client.post("/api/v1/discretionary-orders", json=payload)
    assert response.status_code == 428
    assert response.json()["detail"].startswith("live_acknowledgement_required:")
    assert asyncio.run(execution.list_deployments()) == ()
