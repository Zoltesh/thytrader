"""HTTP contracts for paper and live strategy deployments."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID  # noqa: TC003 - used in Protocol-matching draft store.

from fastapi.testclient import TestClient
from pydantic import SecretStr

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.strategies.authoring import StrategyDraft, create_reference_draft
from thytrader.strategies.models import StrategyDefinition, StrategyStatus, strategy_fingerprint
from thytrader.strategies.publication import (
    PublishedStrategy,
    StrategyCatalogEntry,
    StrategyPublicationError,
)


class InMemoryDraftStore:
    """Minimal draft store so create_app can satisfy the authoring boundary."""

    def __init__(self) -> None:
        """Start with no drafts."""
        self.drafts: dict[tuple[str, int], StrategyDraft] = {}

    async def create_draft(self, definition: StrategyDefinition) -> StrategyDraft:
        """Record one draft."""
        draft = StrategyDraft(definition=definition, revision=1)
        self.drafts[(str(definition.strategy_id), definition.version)] = draft
        return draft

    async def list_drafts(self) -> tuple[StrategyDraft, ...]:
        """Return saved drafts."""
        return tuple(self.drafts.values())

    async def save_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> StrategyDraft:
        """Replace a current draft."""
        del expected_revision
        draft = StrategyDraft(definition=definition, revision=2)
        self.drafts[(str(definition.strategy_id), definition.version)] = draft
        return draft

    async def delete_draft(self, strategy_id: UUID, version: int) -> None:
        """Remove one draft."""
        self.drafts.pop((str(strategy_id), version), None)


class InMemoryPublicationStore:
    """Load published strategies by fingerprint for deployment tests."""

    def __init__(self) -> None:
        """Start without publications."""
        self.published: dict[str, PublishedStrategy] = {}

    async def publish(self, definition: StrategyDefinition) -> PublishedStrategy:
        """Retain one immutable publication."""
        fingerprint = strategy_fingerprint(definition)
        published = PublishedStrategy(strategy_fingerprint=fingerprint, definition=definition)
        self.published[fingerprint] = published
        return published

    async def publish_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> PublishedStrategy:
        """Unused atomic publish path for this store."""
        del expected_revision
        return await self.publish(definition)

    async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
        """Return one previously published definition or fail."""
        published = self.published.get(strategy_fingerprint_value)
        if published is None:
            raise StrategyPublicationError("Published strategy was not found.")
        return published

    async def list_published(self, *, include_archived: bool) -> tuple[StrategyCatalogEntry, ...]:
        """Return every retained publication."""
        del include_archived
        return tuple(
            StrategyCatalogEntry(
                strategy_fingerprint=fingerprint,
                definition=item.definition,
                archived_at=None,
            )
            for fingerprint, item in self.published.items()
        )

    async def archive(self, strategy_fingerprint_value: str) -> StrategyCatalogEntry:
        """Unused archive path for this store."""
        published = await self.load(strategy_fingerprint_value)
        return StrategyCatalogEntry(
            strategy_fingerprint=published.strategy_fingerprint,
            definition=published.definition,
            archived_at=datetime(2026, 1, 2, tzinfo=UTC),
        )


def _published_strategy() -> StrategyDefinition:
    """Return the reference strategy marked published."""
    draft = create_reference_draft(now=datetime(2026, 1, 1, tzinfo=UTC))
    return StrategyDefinition.model_validate(
        {**draft.model_dump(mode="python"), "status": StrategyStatus.PUBLISHED.value}
    )


def _client(
    publication: InMemoryPublicationStore,
    execution: InMemoryExecutionStore,
    *,
    live_credentials: bool = False,
) -> TestClient:
    """Build an API client with in-memory publication and execution stores."""
    settings = Settings(_env_file=None)
    if live_credentials:
        settings = Settings(
            _env_file=None,
            coinbase_api_key_name=SecretStr("key"),
            coinbase_api_private_key=SecretStr("secret"),
        )
    app = create_app(
        settings,
        strategy_store=publication,
        strategy_draft_store=InMemoryDraftStore(),
        execution_store=execution,
    )
    return TestClient(app)


def test_paper_deployment_persists_and_updates_library_status() -> None:
    """POST paper starts a running deployment and the library reports that status."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)

    with _client(publication, execution) as client:
        publication.published[fingerprint] = PublishedStrategy(
            strategy_fingerprint=fingerprint, definition=definition
        )
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )
        listed = client.get("/api/v1/deployments")
        fetched = client.get(f"/api/v1/deployments/{created.json()['id']}")
        library = client.get("/api/v1/strategies")

    assert created.status_code == 201
    body = created.json()
    assert body["mode"] == "paper"
    assert body["status"] == "running"
    assert body["phase"] == "flat"
    assert body["cash"] == "10000"
    assert listed.status_code == 200
    assert listed.json()["deployments"][0]["id"] == body["id"]
    assert fetched.status_code == 200
    assert fetched.json()["orders"] == []
    entry = next(
        item
        for item in library.json()["strategies"]
        if item["strategy_id"] == str(definition.strategy_id)
    )
    assert entry["paper_live"] == {"paper": "running", "live": "unavailable"}


def test_pause_resume_and_stop_deployment() -> None:
    """Pause, resume, and stop follow the documented lifecycle."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        created = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": fingerprint,
                "mode": "paper",
                "paper_starting_cash": "5000",
            },
        )
        deployment_id = created.json()["id"]
        paused = client.post(f"/api/v1/deployments/{deployment_id}/pause")
        resumed = client.post(f"/api/v1/deployments/{deployment_id}/resume")
        stopped = client.post(f"/api/v1/deployments/{deployment_id}/stop")
        rejected = client.post(f"/api/v1/deployments/{deployment_id}/resume")

    assert paused.json()["status"] == "paused"
    assert resumed.json()["status"] == "running"
    assert stopped.json()["status"] == "stopped"
    assert rejected.status_code == 409


def test_duplicate_running_paper_deployment_conflicts() -> None:
    """Only one running paper deployment is allowed per strategy."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )
    payload = {
        "strategy_fingerprint": fingerprint,
        "mode": "paper",
        "paper_starting_cash": "10000",
    }

    with _client(publication, execution) as client:
        first = client.post("/api/v1/deployments", json=payload)
        second = client.post("/api/v1/deployments", json=payload)

    assert first.status_code == 201
    assert second.status_code == 409


def test_live_deployment_requires_credentials() -> None:
    """Live mode is rejected until Coinbase credentials are configured."""
    publication = InMemoryPublicationStore()
    execution = InMemoryExecutionStore()
    definition = _published_strategy()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = PublishedStrategy(
        strategy_fingerprint=fingerprint, definition=definition
    )

    with _client(publication, execution) as client:
        denied = client.post(
            "/api/v1/deployments",
            json={"strategy_fingerprint": fingerprint, "mode": "live"},
        )

    with _client(publication, execution, live_credentials=True) as client:
        allowed = client.post(
            "/api/v1/deployments",
            json={"strategy_fingerprint": fingerprint, "mode": "live"},
        )

    assert denied.status_code == 409
    assert allowed.status_code == 201
    assert allowed.json()["mode"] == "live"
    assert allowed.json()["cash"] == "0"


def test_unknown_fingerprint_is_not_found() -> None:
    """Deploying an unpublished fingerprint fails closed."""
    with _client(InMemoryPublicationStore(), InMemoryExecutionStore()) as client:
        missing = client.post(
            "/api/v1/deployments",
            json={
                "strategy_fingerprint": "sha256:" + "a" * 64,
                "mode": "paper",
                "paper_starting_cash": "10000",
            },
        )
        unknown = client.get("/api/v1/deployments/00000000-0000-7000-8000-000000000001")

    assert missing.status_code == 404
    assert unknown.status_code == 404
