"""HTTP contracts for the per-bar decision timeline (ADR 0087)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from tests.execution.test_decision_store import make_decision
from tests.strategy_fakes import SeededStrategyStore
from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import DecisionOutcome
from thytrader.strategies.authoring import create_template_strategy
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    RuntimePhase,
)

_NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _deployment(deployment_id: UUID, strategy_id: UUID) -> Deployment:
    """One paper strategy book row."""
    return Deployment(
        id=deployment_id,
        strategy_fingerprint=f"sha256:{'b' * 64}",
        strategy_id=strategy_id,
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.FLAT,
        created_at=_NOW,
        updated_at=_NOW,
        timeframe="1h",
        paper_starting_cash=Decimal("10000"),
    )


def _seeded() -> tuple[TestClient, UUID, UUID, UUID]:
    """App with one strategy, two of its bots, and a five-bar journal."""
    strategies = SeededStrategyStore()
    definition = create_template_strategy(now=_NOW)
    strategies.seed_definition(definition)
    strategy_id = definition.strategy_id
    execution = InMemoryExecutionStore()
    journal = InMemoryDecisionJournalStore()
    first, second = uuid4(), uuid4()

    async def seed() -> None:
        await execution.create_deployment(_deployment(first, strategy_id))
        await execution.create_deployment(_deployment(second, strategy_id))
        outcomes = (
            DecisionOutcome.NO_SIGNAL,
            DecisionOutcome.ENTRY_SIGNAL,
            DecisionOutcome.HOLDING,
            DecisionOutcome.EXIT,
            DecisionOutcome.ENTRY_BLOCKED,
        )
        for hour, outcome in enumerate(outcomes):
            await journal.upsert(
                make_decision(
                    deployment_id=first, hour=hour, outcome=outcome, strategy_id=strategy_id
                )
            )
        await journal.upsert(make_decision(deployment_id=second, hour=9, strategy_id=strategy_id))

    asyncio.run(seed())
    app = create_app(
        Settings(_env_file=None),
        strategy_store=strategies,
        execution_store=execution,
        decision_journal_store=journal,
    )
    return TestClient(app), strategy_id, first, second


def test_deployment_decisions_page_newest_first_with_cursor() -> None:
    """Pages walk from the newest bar to older bars via ``next_cursor``."""
    client, _strategy_id, first, _second = _seeded()
    with client:
        page = client.get(f"/api/v1/deployments/{first}/decisions", params={"limit": 2})
        assert page.status_code == 200
        body = page.json()
        assert body["storage"] == "available"
        assert body["returned"] == 2
        assert [row["outcome"] for row in body["decisions"]] == ["entry_blocked", "exit"]
        assert body["decisions"][0]["schema_version"] == "thytrader-bar-decision-v1"
        older = client.get(
            f"/api/v1/deployments/{first}/decisions",
            params={"limit": 10, "cursor": body["next_cursor"]},
        ).json()
        assert [row["outcome"] for row in older["decisions"]] == [
            "holding",
            "entry_signal",
            "no_signal",
        ]
        assert older["next_cursor"] is None


def test_deployment_decisions_filter_repeated_outcomes() -> None:
    """``outcome`` repeats: Trades is ``entry_signal`` plus ``exit``."""
    client, _strategy_id, first, _second = _seeded()
    with client:
        response = client.get(
            f"/api/v1/deployments/{first}/decisions",
            params=[("outcome", "entry_signal"), ("outcome", "exit")],
        )
    assert [row["outcome"] for row in response.json()["decisions"]] == ["exit", "entry_signal"]


def test_deployment_decisions_reject_unknown_bots_bad_cursors_and_outcomes() -> None:
    """404 unknown deployment, 400 malformed cursor, 422 unknown outcome."""
    client, _strategy_id, first, _second = _seeded()
    with client:
        missing = client.get(f"/api/v1/deployments/{uuid4()}/decisions")
        bad_cursor = client.get(
            f"/api/v1/deployments/{first}/decisions", params={"cursor": "garbage"}
        )
        bad_outcome = client.get(
            f"/api/v1/deployments/{first}/decisions", params={"outcome": "maybe"}
        )
    assert missing.status_code == 404
    assert bad_cursor.status_code == 400
    assert bad_outcome.status_code == 422


def test_strategy_decisions_aggregate_bots_and_filter_one() -> None:
    """The strategy timeline interleaves its bots; ``deployment_id`` narrows it."""
    client, strategy_id, first, second = _seeded()
    with client:
        everything = client.get(f"/api/v1/strategies/{strategy_id}/decisions").json()
        narrowed = client.get(
            f"/api/v1/strategies/{strategy_id}/decisions",
            params={"deployment_id": str(first), "outcome": "exit"},
        ).json()
        unknown = client.get(f"/api/v1/strategies/{uuid4()}/decisions")
    assert everything["strategy_id"] == str(strategy_id)
    assert everything["returned"] == 6
    assert everything["decisions"][0]["deployment_id"] == str(second)
    assert narrowed["deployment_id"] == str(first)
    assert [row["outcome"] for row in narrowed["decisions"]] == ["exit"]
    assert unknown.status_code == 404


def test_operator_decisions_report_wraps_the_same_records() -> None:
    """``GET /api/v1/operator/decisions`` returns a v1 ``decisions`` report."""
    client, strategy_id, first, _second = _seeded()
    with client:
        report = client.get(
            "/api/v1/operator/decisions",
            params=[("deployment_id", str(first)), ("outcome", "holding"), ("limit", "5")],
        ).json()
        by_strategy = client.get(
            "/api/v1/operator/decisions", params={"strategy_id": str(strategy_id)}
        ).json()
        overview = client.get("/api/v1/operator/decisions").json()
    assert report["schema_version"] == "thytrader-operator-report-v1"
    assert report["report_kind"] == "decisions"
    assert report["overall_status"] == "healthy"
    payload = report["payload"]
    assert payload["storage"] == "available"
    assert payload["outcomes"] == ["holding"]
    assert [row["outcome"] for row in payload["decisions"]] == ["holding"]
    assert payload["retention_max_rows_per_deployment"] == 20_000
    assert payload["retention_max_age_days"] == 180
    assert len(by_strategy["payload"]["decisions"]) == 6
    assert len(overview["payload"]["decisions"]) == 6


def test_storage_free_api_reports_unavailable_instead_of_failing() -> None:
    """Without a database the timeline is empty and labeled unavailable."""
    strategies = SeededStrategyStore()
    definition = create_template_strategy(now=_NOW)
    strategies.seed_definition(definition)
    execution = InMemoryExecutionStore()
    deployment_id = uuid4()
    asyncio.run(execution.create_deployment(_deployment(deployment_id, definition.strategy_id)))
    app = create_app(Settings(_env_file=None), strategy_store=strategies, execution_store=execution)
    with TestClient(app) as client:
        body = client.get(f"/api/v1/deployments/{deployment_id}/decisions").json()
        report = client.get("/api/v1/operator/decisions").json()
    assert body["storage"] == "unavailable"
    assert body["decisions"] == []
    assert report["payload"]["storage"] == "unavailable"
    assert report["overall_status"] == "failed"
    assert report["components"][0]["reason_code"] == "DECISION_STORAGE_UNAVAILABLE"
