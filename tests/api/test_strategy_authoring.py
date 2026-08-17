"""Behavioral tests for browser-facing strategy authoring contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from uuid import UUID

from thytrader.api.app import create_app
from thytrader.backtest.submission import (
    BacktestSubmissionRequest,
    BacktestSubmissionResult,
)
from thytrader.config import Settings
from thytrader.strategies.authoring import StrategyDraft
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.publication import PublishedStrategy, StrategyPublicationError


class InMemoryBacktestSubmitter:
    """Return deterministic immutable identities from the route submission boundary."""

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Record no execution and return the fixed evidence used by this route test."""
        del request
        return BacktestSubmissionResult(
            run_fingerprint="sha256:" + "a" * 64,
            result_fingerprint="sha256:" + "b" * 64,
        )


class InMemoryStrategyPublicationStore:
    """Capture immutable publication requests without a database in route tests."""

    def __init__(self, draft_store: InMemoryStrategyDraftStore) -> None:
        """Start without any published definition."""
        self.draft_store = draft_store
        self.published: StrategyDefinition | None = None

    async def publish(self, definition: StrategyDefinition) -> PublishedStrategy:
        """Return the exact immutable definition under its canonical fingerprint."""
        self.published = definition
        return PublishedStrategy(
            strategy_fingerprint=strategy_fingerprint(definition), definition=definition
        )

    async def publish_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> PublishedStrategy:
        """Publish and consume a current test draft as one operation."""
        key = (str(definition.strategy_id), definition.version)
        current = self.draft_store.drafts.get(key)
        if current is None:
            raise StrategyPublicationError("Strategy draft was not found.")
        if current.revision != expected_revision:
            raise StrategyPublicationError("Strategy draft revision conflict.")
        published = StrategyDefinition.model_validate(
            {**definition.model_dump(mode="python"), "status": "published"}
        )
        result = await self.publish(published)
        self.draft_store.drafts.pop(key)
        return result


class InMemoryStrategyDraftStore:
    """Persist revision-guarded drafts without PostgreSQL."""

    def __init__(self) -> None:
        """Start with no saved browser drafts."""
        self.drafts: dict[tuple[str, int], StrategyDraft] = {}

    async def create_draft(self, definition: StrategyDefinition) -> StrategyDraft:
        """Store and return one newly created draft."""
        draft = StrategyDraft(definition=definition, revision=1)
        self.drafts[(str(definition.strategy_id), definition.version)] = draft
        return draft

    async def list_drafts(self) -> tuple[StrategyDraft, ...]:
        """Return each saved draft in test insertion order."""
        return tuple(self.drafts.values())

    async def save_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> StrategyDraft:
        """Replace an existing test draft when its revision matches."""
        key = (str(definition.strategy_id), definition.version)
        existing = self.drafts.get(key)
        if existing is None:
            raise RuntimeError("Strategy draft was not found.")
        if existing.revision != expected_revision:
            raise RuntimeError("Strategy draft revision conflict.")
        saved = StrategyDraft(definition=definition, revision=expected_revision + 1)
        self.drafts[key] = saved
        return saved

    async def delete_draft(self, strategy_id: UUID, version: int) -> None:
        """Consume a published test draft."""
        self.drafts.pop((str(strategy_id), version), None)


def test_draft_discovery_without_durable_storage_fails_closed() -> None:
    """Draft discovery exposes a controlled unavailable response without a database."""
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        response = client.get("/api/v1/strategies?status=draft")

    assert response.status_code == 503
    assert response.json() == {"detail": "Strategy lifecycle storage is unavailable."}


def test_strategy_creation_returns_a_durable_conservative_draft() -> None:
    """Creating a browser draft supplies server-owned identity and safe reference defaults."""
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=InMemoryStrategyDraftStore(),
    )

    with TestClient(app) as client:
        response = client.post("/api/v1/strategies")

    assert response.status_code == 201
    payload = response.json()["strategy"]
    assert payload["status"] == "draft"
    assert payload["schema_version"] == "1.0"
    assert payload["instrument"]["product_id"] == "BTC-USD"
    assert payload["timeframe"] == "1h"
    assert payload["entry"]["side"] == "long"
    assert payload["entry"]["max_open_positions"] == 1
    assert payload["portfolio_limits"]["max_concurrent_positions"] == 1
    assert payload["exits"]["trailing_stop"] == {"enabled": False}
    assert "fingerprint" not in payload


def test_strategy_publication_turns_the_matching_draft_into_immutable_evidence() -> None:
    """A publication delegates the matching draft to the immutable store."""
    draft_store = InMemoryStrategyDraftStore()
    store = InMemoryStrategyPublicationStore(draft_store)
    app = create_app(
        Settings(_env_file=None),
        strategy_store=store,
        strategy_draft_store=draft_store,
    )

    with TestClient(app) as client:
        draft_response = client.post("/api/v1/strategies")
        draft = draft_response.json()["strategy"]
        response = client.post(
            f"/api/v1/strategies/{draft['strategy_id']}/publish",
            json={"strategy": draft, "revision": 1},
        )

    assert response.status_code == 201
    assert store.published is not None
    assert store.published.status.value == "published"
    payload = response.json()
    assert payload["strategy_fingerprint"] == strategy_fingerprint(store.published)
    assert payload["strategy"]["status"] == "published"


def test_backtest_submission_returns_immutable_run_and_result_identities() -> None:
    """The browser route delegates execution to an application service, not the UI."""
    app = create_app(
        Settings(_env_file=None),
        backtest_submitter=InMemoryBacktestSubmitter(),
    )
    request = {
        "strategy_fingerprint": "sha256:" + "a" * 64,
        "dataset_fingerprint": "sha256:" + "b" * 64,
        "evaluation_start": "2026-08-01T00:00:00Z",
        "evaluation_end": "2026-08-02T00:00:00Z",
        "initial_quote_balance": "10000",
        "maker_fee_rate": "0.001",
        "taker_fee_rate": "0.002",
        "fixed_slippage_bps": "1",
    }

    with TestClient(app) as client:
        response = client.post("/api/v1/backtests", json=request)

    assert response.status_code == 201
    assert response.json() == {
        "run_fingerprint": "sha256:" + "a" * 64,
        "result_fingerprint": "sha256:" + "b" * 64,
    }
