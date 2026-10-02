"""HTTP contract for deploying portfolios and the manager's proposals (ADR 0091)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.portfolios.store import InMemoryPortfolioStore
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.strategies.memory_store import InMemoryStrategyStore

if TYPE_CHECKING:
    from collections.abc import Iterator

    from thytrader.strategies.library import StrategyRecord

JsonBody = dict[str, Any]
"""A decoded TestClient JSON body (dynamic at this HTTP boundary; asserted field by field)."""


class Harness:
    """One app with in-memory strategies, deployments, portfolios, and risk policy."""

    def __init__(self, client: TestClient, strategies: InMemoryStrategyStore) -> None:
        """Keep the client and the strategy store."""
        self.client = client
        self.strategies = strategies

    def strategy(self, product_id: str) -> StrategyRecord:
        """Create one valid 1h template strategy."""
        definition = create_template_strategy(
            product_id=product_id, timeframe="1h", template="ema-trend"
        )
        return asyncio.run(create_strategy_from_definition(self.strategies, definition))

    def portfolio(self, *, mode: str = "paper", **manager: bool) -> JsonBody:
        """A portfolio with BTC (50%) and ETH (30%) sleeves on 1,000 capital."""
        response = self.client.post(
            "/api/v1/portfolios",
            json={
                "name": "Core",
                "mode": mode,
                "capital_quote": "1000",
                "cash_reserve_fraction": "0.2",
                "manager": {"mandate": "Grow steadily.", "permissions": manager},
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        for product_id, weight in (("BTC-USDC", "0.5"), ("ETH-USDC", "0.3")):
            record = self.strategy(product_id)
            added = self.client.post(
                f"/api/v1/portfolios/{body['portfolio_id']}/sleeves",
                json={
                    "revision": body["revision"],
                    "strategy_id": str(record.strategy_id),
                    "weight_fraction": weight,
                },
            )
            assert added.status_code == 201, added.text
            body = added.json()
        return body

    def start(self, body: JsonBody, **extra: object) -> Any:
        """POST start at the body's revision."""
        return self.client.post(
            f"/api/v1/portfolios/{body['portfolio_id']}/start",
            json={"revision": body["revision"], **extra},
        )


@pytest.fixture
def harness() -> Iterator[Harness]:
    """An app with live credentials configured and a published risk policy."""
    strategies = InMemoryStrategyStore()
    execution = InMemoryExecutionStore()
    risk = InMemoryRiskPolicyStore()
    asyncio.run(risk.publish(compiled_default_risk_policy()))
    app = create_app(
        Settings(
            _env_file=None,
            coinbase_api_key_name=SecretStr("key"),
            coinbase_api_private_key=SecretStr("secret"),
        ),
        strategy_store=strategies,
        execution_store=execution,
        risk_policy_store=risk,
        portfolio_store=InMemoryPortfolioStore(strategies=strategies, execution=execution),
    )
    with TestClient(app) as client:
        yield Harness(client, strategies)


def test_paper_start_status_controls_and_portfolio_state(harness: Harness) -> None:
    """Start, read, pause one sleeve, resume, flatten-stop, and the portfolio reports it."""
    body = harness.portfolio()
    pid = body["portfolio_id"]
    assert body["deployable"] is True
    assert body["deployment_state"] == "not_deployed"
    started = harness.start(body)
    assert started.status_code == 200, started.text
    payload = started.json()
    assert [item["outcome"] for item in payload["outcomes"]] == ["started", "started"]
    deployment = payload["deployment"]
    assert deployment["state"] == "running"
    capitals = [sleeve["deployment"]["paper_starting_cash"] for sleeve in deployment["sleeves"]]
    assert capitals == ["500", "300"]
    assert deployment["breaker"]["latched"] is False
    assert deployment["exposure"]["cap_quote"] == "1000"
    bots = harness.client.get("/api/v1/deployments").json()["deployments"]
    assert {bot["portfolio_id"] for bot in bots} == {pid}
    assert harness.client.get(f"/api/v1/portfolios/{pid}").json()["deployment_state"] == "running"
    sleeve = body["sleeves"][0]["sleeve_id"]
    paused = harness.client.post(f"/api/v1/portfolios/{pid}/sleeves/{sleeve}/pause")
    assert paused.status_code == 200, paused.text
    assert paused.json()["deployment"]["state"] == "partially_running"
    resumed = harness.client.post(f"/api/v1/portfolios/{pid}/resume", json={})
    assert [item["outcome"] for item in resumed.json()["outcomes"]] == ["resumed", "unchanged"]
    stopped = harness.client.post(f"/api/v1/portfolios/{pid}/stop?flatten=true")
    assert stopped.status_code == 200
    assert stopped.json()["deployment"]["state"] == "stopped"
    lifecycle = {
        sleeve["deployment"]["lifecycle_command"]
        for sleeve in stopped.json()["deployment"]["sleeves"]
    }
    assert lifecycle == {"flatten"}
    again = harness.client.post(f"/api/v1/portfolios/{pid}/pause")
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "portfolio_not_deployed"
    reset = harness.client.post(f"/api/v1/portfolios/{pid}/breaker/reset")
    assert reset.status_code == 409
    assert reset.json()["detail"]["code"] == "portfolio_breaker_not_latched"


def test_live_start_and_resume_need_the_acknowledgement(harness: Harness) -> None:
    """HTTP 428 without i_understand_live; the same flag re-arms a live resume."""
    body = harness.portfolio(mode="live")
    pid = body["portfolio_id"]
    refused = harness.start(body)
    assert refused.status_code == 428
    assert "live_acknowledgement_required" in refused.json()["detail"]
    started = harness.start(body, i_understand_live=True)
    assert started.status_code == 200, started.text
    allocations = [
        sleeve["deployment"]["allocated_capital"]
        for sleeve in started.json()["deployment"]["sleeves"]
    ]
    assert allocations == ["500", "300"]
    assert harness.client.post(f"/api/v1/portfolios/{pid}/pause").status_code == 200
    assert harness.client.post(f"/api/v1/portfolios/{pid}/resume").status_code == 428
    resumed = harness.client.post(
        f"/api/v1/portfolios/{pid}/resume", json={"i_understand_live": True}
    )
    assert resumed.status_code == 200


def test_rejected_starts_list_every_sleeve_problem(harness: Harness) -> None:
    """A sleeve whose strategy already runs standalone blocks the start with 422."""
    body = harness.portfolio()
    strategy_id = body["sleeves"][0]["strategy_id"]
    standalone = harness.client.post(
        "/api/v1/deployments",
        json={"strategy_id": strategy_id, "mode": "paper", "paper_starting_cash": "100"},
    )
    assert standalone.status_code == 201, standalone.text
    response = harness.start(body)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "portfolio_start_rejected"
    assert [(item["code"], item["strategy_id"]) for item in detail["problems"]] == [
        ("strategy_busy", strategy_id)
    ]
    stale = harness.client.post(
        f"/api/v1/portfolios/{body['portfolio_id']}/start", json={"revision": 1}
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "portfolio_revision_conflict"


def test_deployed_portfolios_cannot_be_deleted(harness: Harness) -> None:
    """DELETE is refused while sleeves run, and allowed once stopped."""
    body = harness.portfolio()
    pid = body["portfolio_id"]
    assert harness.start(body).status_code == 200
    refused = harness.client.delete(f"/api/v1/portfolios/{pid}?revision={body['revision']}")
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "portfolio_deployed"
    harness.client.post(f"/api/v1/portfolios/{pid}/stop")
    deleted = harness.client.delete(f"/api/v1/portfolios/{pid}?revision={body['revision']}")
    assert deleted.status_code == 200


def test_proposals_submit_approve_decline_and_briefing(harness: Harness) -> None:
    """The manager proposes; a person decides; the briefing reads it all in one call."""
    body = harness.portfolio()
    pid = body["portfolio_id"]
    weights = [
        {"sleeve_id": body["sleeves"][0]["sleeve_id"], "weight_fraction": "0.4"},
        {"sleeve_id": body["sleeves"][1]["sleeve_id"], "weight_fraction": "0.4"},
    ]
    submitted = harness.client.post(
        f"/api/v1/portfolios/{pid}/proposals",
        json={
            "revision": body["revision"],
            "change": {"kind": "rebalance", "weights": weights},
            "rationale": "Even out the sleeves.",
            "evidence": [{"kind": "backtest_result", "ref": "sha256:" + "e" * 64}],
        },
    )
    assert submitted.status_code == 201, submitted.text
    proposal = submitted.json()["proposal"]
    assert (proposal["status"], proposal["kind"]) == ("pending", "rebalance")
    assert "may_rebalance is off" in proposal["approval_reason"]
    listed = harness.client.get(f"/api/v1/portfolios/{pid}/proposals?status=pending").json()
    assert [item["proposal_id"] for item in listed["proposals"]] == [proposal["proposal_id"]]
    approved = harness.client.post(
        f"/api/v1/portfolios/{pid}/proposals/{proposal['proposal_id']}/approve",
        json={"note": "Yes."},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["proposal"]["status"] == "applied"
    assert approved.json()["portfolio_revision"] == body["revision"] + 1
    declined = harness.client.post(
        f"/api/v1/portfolios/{pid}/proposals/{proposal['proposal_id']}/decline"
    )
    assert declined.status_code == 409
    assert declined.json()["detail"]["code"] == "portfolio_proposal_not_pending"
    missing = harness.client.get(
        f"/api/v1/portfolios/{pid}/proposals/01978a3e-5f2c-7d10-b3a4-0000000000ff"
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "portfolio_proposal_not_found"
    briefing = harness.client.get(f"/api/v1/portfolios/{pid}/briefing")
    assert briefing.status_code == 200, briefing.text
    payload = briefing.json()
    assert payload["contract"] == "thytrader-portfolio-briefing-v1"
    assert payload["permissions"]["order_authority"] is False
    assert [sleeve["weight_fraction"] for sleeve in payload["sleeves"]] == ["0.4", "0.4"]
    assert payload["journal"][0]["kind"] == "weights_changed"


def test_order_shaped_proposals_are_refused(harness: Harness) -> None:
    """The proposals API has no order authority and says so."""
    body = harness.portfolio()
    response = harness.client.post(
        f"/api/v1/portfolios/{body['portfolio_id']}/proposals",
        json={
            "revision": body["revision"],
            "change": {"kind": "place_order", "side": "buy"},
            "rationale": "Buy now.",
        },
    )
    assert response.status_code == 422
    assert "never places orders" in response.text
