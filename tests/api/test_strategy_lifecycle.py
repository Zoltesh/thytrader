"""Behavioral tests for durable browser strategy-draft recovery."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from uuid import UUID


from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.persistence.postgres_strategies import PostgresStrategyPublicationStore
from thytrader.strategies.authoring import StrategyDraft, create_reference_draft
from thytrader.strategies.models import StrategyDefinition, StrategyStatus, strategy_fingerprint
from thytrader.strategies.publication import (
    PublishedStrategy,
    StrategyCatalogEntry,
    StrategyPublicationError,
)


class InMemoryDraftStore:
    """Persist revision-guarded draft definitions for route lifecycle tests."""

    def __init__(self) -> None:
        """Start with an empty durable-draft stand-in."""
        self.drafts: dict[tuple[str, int], StrategyDraft] = {}
        self.create_result: StrategyDraft | None = None
        self.list_result: tuple[StrategyDraft, ...] | None = None
        self.save_result: StrategyDraft | None = None

    async def create_draft(self, definition: StrategyDefinition) -> StrategyDraft:
        """Record and return one editable draft at revision one."""
        draft = StrategyDraft(definition=definition, revision=1)
        self.drafts[(str(definition.strategy_id), definition.version)] = draft
        return self.create_result or draft

    async def list_drafts(self) -> tuple[StrategyDraft, ...]:
        """Return saved drafts in their creation order."""
        return self.list_result if self.list_result is not None else tuple(self.drafts.values())

    async def save_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> StrategyDraft:
        """Replace a draft only when the caller holds its current revision."""
        key = (str(definition.strategy_id), definition.version)
        existing = self.drafts.get(key)
        if existing is None:
            raise RuntimeError("Strategy draft was not found.")
        if existing.revision != expected_revision:
            raise RuntimeError("Strategy draft revision conflict.")
        saved = StrategyDraft(definition=definition, revision=expected_revision + 1)
        self.drafts[key] = saved
        return self.save_result or saved

    async def delete_draft(self, strategy_id: UUID, version: int) -> None:
        """Remove one draft after its immutable publication is accepted."""
        self.drafts.pop((str(strategy_id), version), None)


class InMemoryPublicationStore:
    """Publish and archive immutable definitions for lifecycle contract tests."""

    def __init__(
        self,
        draft_store: InMemoryDraftStore | None = None,
        *,
        fingerprint_override: str | None = None,
        archive_fingerprint_override: str | None = None,
    ) -> None:
        """Start without immutable publications or archive markers."""
        self.draft_store = draft_store
        self.fingerprint_override = fingerprint_override
        self.archive_fingerprint_override = archive_fingerprint_override
        self.published: dict[str, PublishedStrategy] = {}
        self.archived: dict[str, datetime | None] = {}
        self.archive_timestamp: datetime | None = datetime(2026, 8, 15, 19, tzinfo=UTC)

    async def publish(self, definition: StrategyDefinition) -> PublishedStrategy:
        """Persist one immutable publication addressed by its canonical fingerprint."""
        fingerprint = self.fingerprint_override or strategy_fingerprint(definition)
        published = PublishedStrategy(strategy_fingerprint=fingerprint, definition=definition)
        self.published[fingerprint] = published
        return published

    async def publish_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> PublishedStrategy:
        """Atomically publish and consume a current in-memory draft."""
        published_definition = StrategyDefinition.model_validate(
            {**definition.model_dump(mode="python"), "status": StrategyStatus.PUBLISHED}
        )
        fingerprint = self.fingerprint_override or strategy_fingerprint(published_definition)
        existing_publication = self.published.get(fingerprint)
        if self.draft_store is None:
            raise StrategyPublicationError("Strategy publication storage is unavailable.")
        key = (str(definition.strategy_id), definition.version)
        current = self.draft_store.drafts.get(key)
        if current is None:
            if existing_publication is not None:
                return existing_publication
            raise StrategyPublicationError("Strategy draft was not found.")
        if current.revision != expected_revision:
            raise StrategyPublicationError("Strategy draft revision conflict.")
        published = PublishedStrategy(
            strategy_fingerprint=fingerprint, definition=published_definition
        )
        self.published[fingerprint] = published
        self.draft_store.drafts.pop(key)
        return published

    async def list_published(self, *, include_archived: bool) -> tuple[StrategyCatalogEntry, ...]:
        """Return published evidence, omitting archived entries unless explicitly requested."""
        return tuple(
            StrategyCatalogEntry(
                strategy_fingerprint=fingerprint,
                definition=published.definition,
                archived_at=self.archived.get(fingerprint),
            )
            for fingerprint, published in self.published.items()
            if include_archived or fingerprint not in self.archived
        )

    async def archive(self, strategy_fingerprint_value: str) -> StrategyCatalogEntry:
        """Mark one existing immutable publication inactive without mutating it."""
        published = self.published.get(strategy_fingerprint_value)
        if published is None:
            raise RuntimeError("Published strategy was not found.")
        self.archived[strategy_fingerprint_value] = self.archive_timestamp
        return StrategyCatalogEntry(
            strategy_fingerprint=(self.archive_fingerprint_override or strategy_fingerprint_value),
            definition=published.definition,
            archived_at=self.archive_timestamp,
        )


def test_strategy_creation_persists_a_draft_that_the_browser_can_recover() -> None:
    """A reference draft survives the create request and is discoverable in the library."""
    draft_store = InMemoryDraftStore()
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=draft_store,
        strategy_store=InMemoryPublicationStore(draft_store),
    )

    with TestClient(app) as client:
        created = client.post("/api/v1/strategies")
        library = client.get("/api/v1/strategies")

    assert created.status_code == 201
    assert created.json()["revision"] == 1
    assert library.status_code == 200
    created_draft = created.json()["strategy"]
    entry = library.json()["strategies"][0]
    assert entry["strategy_id"] == created_draft["strategy_id"]
    assert entry["name"] == created_draft["name"]
    assert entry["product_id"] == "BTC-USD"
    assert entry["timeframe"] == "1h"
    assert entry["latest_version"] == 1
    assert entry["status"] == "draft"
    assert entry["latest_fingerprint"] is None
    assert entry["archived"] is False
    assert entry["backtest"] is None
    assert entry["paper_live"] == {"paper": "unavailable", "live": "unavailable"}
    assert (
        entry["summary"]
        == "BTC-USD · 1h · EMA(20) crosses above EMA(50) · RSI ≥ 50 · 0.5% risk · $10-$100"
    )


def test_strategy_creation_rejects_forged_draft_store_definitions() -> None:
    """The create boundary rejects invalid or substituted store content."""
    reference = create_reference_draft()
    forged_definitions = (
        reference.model_copy(update={"version": 0}),
        create_reference_draft(),
    )

    for forged in forged_definitions:
        draft_store = InMemoryDraftStore()
        draft_store.create_result = StrategyDraft(definition=forged, revision=1)
        app = create_app(
            Settings(_env_file=None),
            strategy_draft_store=draft_store,
            strategy_store=InMemoryPublicationStore(draft_store),
        )

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/api/v1/strategies")

        assert response.status_code == 503
        assert response.json() == {"detail": "Strategy draft storage is unavailable."}


def test_strategy_listing_rejects_forged_content_and_revision() -> None:
    """Every discovered draft is revalidated, including its strict positive revision."""
    valid = create_reference_draft()
    forged_drafts = (
        StrategyDraft(definition=valid.model_copy(update={"version": 0}), revision=1),
        StrategyDraft(definition=valid, revision=True),
    )

    for forged in forged_drafts:
        draft_store = InMemoryDraftStore()
        draft_store.list_result = (forged,)
        app = create_app(
            Settings(_env_file=None),
            strategy_draft_store=draft_store,
            strategy_store=InMemoryPublicationStore(draft_store),
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/strategies?status=draft")

        assert response.status_code == 503
        assert response.json() == {"detail": "Strategy lifecycle storage is unavailable."}


def test_strategy_save_rejects_a_mismatched_store_identity() -> None:
    """A save response must return the exact canonical definition supplied by the caller."""
    draft_store = InMemoryDraftStore()
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=draft_store,
        strategy_store=InMemoryPublicationStore(draft_store),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.post("/api/v1/strategies").json()
        definition = StrategyDefinition.model_validate(created["strategy"])
        mismatched = definition.model_copy(
            update={"strategy_id": create_reference_draft().strategy_id}
        )
        draft_store.save_result = StrategyDraft(definition=mismatched, revision=2)
        response = client.put(
            f"/api/v1/strategies/{definition.strategy_id}/versions/{definition.version}",
            json={"strategy": created["strategy"], "revision": created["revision"]},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Strategy draft storage is unavailable."}


@pytest.mark.parametrize(
    ("rowcount", "failing_method"),
    ((1, "_load_draft"), (0, "_draft_exists")),
)
def test_postgres_save_follow_up_database_failures_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
    rowcount: int,
    failing_method: str,
) -> None:
    """Post-update reads cannot leak database errors through the draft HTTP boundary."""
    engine = MagicMock()
    connection = AsyncMock()
    connection.execute.return_value = MagicMock(rowcount=rowcount)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine.begin.return_value = transaction
    store = PostgresStrategyPublicationStore(engine)
    monkeypatch.setattr(
        store,
        failing_method,
        AsyncMock(side_effect=SQLAlchemyError("driver detail")),
    )
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=store,
        strategy_store=InMemoryPublicationStore(),
    )
    definition = create_reference_draft()

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put(
            f"/api/v1/strategies/{definition.strategy_id}/versions/{definition.version}",
            json={"strategy": definition.model_dump(mode="json"), "revision": 1},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Strategy draft storage is unavailable."}


def test_strategy_draft_update_recovers_the_latest_validated_definition() -> None:
    """The browser can save editable changes and recover the latest durable draft."""
    draft_store = InMemoryDraftStore()
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=draft_store,
        strategy_store=InMemoryPublicationStore(draft_store),
    )

    with TestClient(app) as client:
        draft = client.post("/api/v1/strategies").json()["strategy"]
        draft["name"] = "BTC trend with a bounded risk budget"
        draft["sizing"]["risk_fraction"] = "0.01"
        saved = client.put(
            f"/api/v1/strategies/{draft['strategy_id']}/versions/{draft['version']}",
            json={"strategy": draft, "revision": 1},
        )
        recovered = client.get("/api/v1/strategies?status=draft")

    assert saved.status_code == 200
    assert saved.json()["strategy"]["name"] == "BTC trend with a bounded risk budget"
    assert "1% risk" in saved.json()["summary"]
    assert recovered.status_code == 200
    recovered_name = recovered.json()["strategies"][0]["name"]
    assert recovered_name == "BTC trend with a bounded risk budget"


def test_publication_rejects_a_forged_storage_fingerprint() -> None:
    """The HTTP boundary recomputes immutable identity before returning publication evidence."""
    draft_store = InMemoryDraftStore()
    publication_store = InMemoryPublicationStore(
        draft_store,
        fingerprint_override=f"sha256:{'0' * 64}",
    )
    app = create_app(
        Settings(_env_file=None),
        strategy_store=publication_store,
        strategy_draft_store=draft_store,
    )
    with TestClient(app) as client:
        created = client.post("/api/v1/strategies").json()
        response = client.post(
            f"/api/v1/strategies/{created['strategy']['strategy_id']}/publish",
            json={"strategy": created["strategy"], "revision": created["revision"]},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Strategy publication is unavailable."}


def test_strategy_summary_follows_crossover_operands_not_indicator_order() -> None:
    """Reordering declarations cannot reverse the operator-readable entry rule."""
    draft_store = InMemoryDraftStore()
    definition = create_reference_draft()
    reversed_definition = definition.model_copy(
        update={"indicators": tuple(reversed(definition.indicators))}
    )
    draft_store.drafts[(str(reversed_definition.strategy_id), reversed_definition.version)] = (
        StrategyDraft(
            definition=reversed_definition,
            revision=1,
        )
    )
    app = create_app(
        Settings(_env_file=None),
        strategy_store=InMemoryPublicationStore(draft_store),
        strategy_draft_store=draft_store,
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/strategies")

    assert response.status_code == 200
    summary = response.json()["strategies"][0]["summary"]
    assert "EMA(20) crosses above EMA(50)" in summary


def test_published_catalog_rejects_fingerprint_and_status_forgery() -> None:
    """Catalog output is re-bound to canonical published strategy identity at HTTP."""
    draft_store = InMemoryDraftStore()
    publication_store = InMemoryPublicationStore(draft_store)
    definition = create_reference_draft()
    forged_fingerprint = f"sha256:{'1' * 64}"
    publication_store.published[forged_fingerprint] = PublishedStrategy(
        strategy_fingerprint=forged_fingerprint,
        definition=definition,
    )
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=draft_store,
        strategy_store=publication_store,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/strategies")

    assert response.status_code == 503
    assert response.json() == {"detail": "Strategy publication catalog is unavailable."}


def test_archive_response_must_match_the_requested_publication_identity() -> None:
    """Archive output cannot redirect the browser to unrelated immutable evidence."""
    draft_store = InMemoryDraftStore()
    publication_store = InMemoryPublicationStore(
        draft_store,
        archive_fingerprint_override=f"sha256:{'2' * 64}",
    )
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=draft_store,
        strategy_store=publication_store,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.post("/api/v1/strategies").json()
        published = client.post(
            f"/api/v1/strategies/{created['strategy']['strategy_id']}/publish",
            json={"strategy": created["strategy"], "revision": created["revision"]},
        ).json()
        response = client.post(f"/api/v1/strategies/{published['strategy_fingerprint']}/archive")

    assert response.status_code == 503
    assert response.json() == {"detail": "Strategy publication catalog is unavailable."}


def test_draft_cas_and_publication_revisions_reject_json_coercion() -> None:
    """Opaque revision claims accept only strict positive JSON integers."""
    draft_store = InMemoryDraftStore()
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=draft_store,
        strategy_store=InMemoryPublicationStore(draft_store),
    )
    with TestClient(app) as client:
        created = client.post("/api/v1/strategies").json()
        strategy = created["strategy"]
        for revision in (True, "1", 1.0):
            saved = client.put(
                f"/api/v1/strategies/{strategy['strategy_id']}/versions/1",
                json={"strategy": strategy, "revision": revision},
            )
            published = client.post(
                f"/api/v1/strategies/{strategy['strategy_id']}/publish",
                json={"strategy": strategy, "revision": revision},
            )
            assert saved.status_code == 422
            assert published.status_code == 422


def test_strategy_summary_preserves_exact_long_decimal_risk_percentage() -> None:
    """Human-readable financial values do not round under ambient Decimal precision."""
    draft_store = InMemoryDraftStore()
    payload = create_reference_draft().model_dump(mode="python")
    payload["sizing"]["risk_fraction"] = "0.123456789012345678901234567890123456789"
    definition = StrategyDefinition.model_validate(payload)
    draft_store.drafts[(str(definition.strategy_id), definition.version)] = StrategyDraft(
        definition=definition,
        revision=1,
    )
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=draft_store,
        strategy_store=InMemoryPublicationStore(draft_store),
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/strategies?status=draft")

    assert response.status_code == 200
    assert (
        "12.3456789012345678901234567890123456789% risk"
        in response.json()["strategies"][0]["summary"]
    )


def test_stale_browser_save_cannot_overwrite_a_newer_draft_revision() -> None:
    """Optimistic concurrency rejects a stale tab without changing durable content."""
    draft_store = InMemoryDraftStore()
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=draft_store,
        strategy_store=InMemoryPublicationStore(draft_store),
    )

    with TestClient(app) as client:
        created = client.post("/api/v1/strategies").json()
        first = created["strategy"]
        first["name"] = "Newest accepted edit"
        accepted = client.put(
            f"/api/v1/strategies/{first['strategy_id']}/versions/{first['version']}",
            json={"strategy": first, "revision": created["revision"]},
        )
        stale = created["strategy"]
        stale["name"] = "Stale overwrite"
        rejected = client.put(
            f"/api/v1/strategies/{stale['strategy_id']}/versions/{stale['version']}",
            json={"strategy": stale, "revision": created["revision"]},
        )
        recovered = client.get("/api/v1/strategies").json()["strategies"][0]

    assert accepted.status_code == 200
    assert accepted.json()["revision"] == 2
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": "Strategy draft changed; reload before saving."}
    assert recovered["name"] == "Newest accepted edit"
    assert recovered["latest_version"] == 1


def test_publication_rejects_a_draft_that_was_never_durably_saved() -> None:
    """A caller cannot create immutable evidence by supplying an unsaved draft document."""
    draft_store = InMemoryDraftStore()
    publication_store = InMemoryPublicationStore(draft_store)
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=draft_store,
        strategy_store=publication_store,
    )
    unsaved_draft = create_reference_draft()

    with TestClient(app) as client:
        publication = client.post(
            f"/api/v1/strategies/{unsaved_draft.strategy_id}/publish",
            json={"strategy": unsaved_draft.model_dump(mode="json"), "revision": 1},
        )

    assert publication.status_code == 404
    assert publication_store.published == {}


def test_publishing_a_draft_consumes_its_mutable_browser_copy() -> None:
    """Published evidence remains immutable while its corresponding editable draft is consumed."""
    draft_store = InMemoryDraftStore()
    publication_store = InMemoryPublicationStore(draft_store)
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=draft_store,
        strategy_store=publication_store,
    )

    with TestClient(app) as client:
        draft = client.post("/api/v1/strategies").json()["strategy"]
        publication = client.post(
            f"/api/v1/strategies/{draft['strategy_id']}/publish",
            json={"strategy": draft, "revision": 1},
        )
        library = client.get("/api/v1/strategies")

    assert publication.status_code == 201
    assert library.status_code == 200
    entry = library.json()["strategies"][0]
    assert entry["status"] == "published"
    assert entry["strategy_id"] == draft["strategy_id"]
    assert entry["latest_fingerprint"] is not None


def test_archiving_hides_immutable_publication_from_active_browser_selection() -> None:
    """Archive markers hide a publication without changing its content-addressed evidence."""
    draft_store = InMemoryDraftStore()
    publication_store = InMemoryPublicationStore(draft_store)
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=draft_store,
        strategy_store=publication_store,
    )

    with TestClient(app) as client:
        draft = client.post("/api/v1/strategies").json()["strategy"]
        published = client.post(
            f"/api/v1/strategies/{draft['strategy_id']}/publish",
            json={"strategy": draft, "revision": 1},
        ).json()
        archive = client.post(f"/api/v1/strategies/{published['strategy_fingerprint']}/archive")
        history = client.get("/api/v1/strategies")

    assert archive.status_code == 200
    assert archive.json()["strategy_fingerprint"] == published["strategy_fingerprint"]
    assert history.status_code == 200
    historic_entry = history.json()["strategies"][0]
    assert historic_entry["archived"] is True
    assert historic_entry["status"] == "archived"
    assert historic_entry["strategy_id"] == draft["strategy_id"]


@pytest.mark.parametrize(
    "archive_timestamp",
    (None, datetime(2026, 8, 15, 19, tzinfo=UTC).replace(tzinfo=None)),
)
def test_archive_rejects_missing_or_naive_marker_evidence(
    archive_timestamp: datetime | None,
) -> None:
    """Archive success requires a non-null timezone-aware UTC marker from storage."""
    draft_store = InMemoryDraftStore()
    publication_store = InMemoryPublicationStore(draft_store)
    publication_store.archive_timestamp = archive_timestamp
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=draft_store,
        strategy_store=publication_store,
    )

    with TestClient(app) as client:
        created = client.post("/api/v1/strategies").json()
        published = client.post(
            f"/api/v1/strategies/{created['strategy']['strategy_id']}/publish",
            json={"strategy": created["strategy"], "revision": created["revision"]},
        ).json()
        response = client.post(f"/api/v1/strategies/{published['strategy_fingerprint']}/archive")

    assert response.status_code == 503
    assert response.json() == {"detail": "Strategy publication catalog is unavailable."}


@pytest.mark.parametrize(
    "failure",
    (TypeError("driver detail"), ValueError("driver detail")),
)
def test_archive_integrity_exception_failures_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    """Archive storage integrity exceptions cannot leak as uncontrolled 500s."""
    draft_store = InMemoryDraftStore()
    publication_store = InMemoryPublicationStore(draft_store)
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=draft_store,
        strategy_store=publication_store,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.post("/api/v1/strategies").json()
        published = client.post(
            f"/api/v1/strategies/{created['strategy']['strategy_id']}/publish",
            json={"strategy": created["strategy"], "revision": created["revision"]},
        ).json()
        monkeypatch.setattr(publication_store, "archive", AsyncMock(side_effect=failure))
        response = client.post(f"/api/v1/strategies/{published['strategy_fingerprint']}/archive")

    assert response.status_code == 503
    assert response.json() == {"detail": "Strategy publication catalog is unavailable."}


@pytest.mark.parametrize(
    ("operation", "path", "detail"),
    (
        ("create_draft", "/api/v1/strategies", "Strategy draft storage is unavailable."),
        (
            "list_drafts",
            "/api/v1/strategies",
            "Strategy lifecycle storage is unavailable.",
        ),
    ),
)
def test_draft_store_integrity_exception_failures_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    path: str,
    detail: str,
) -> None:
    """Draft-store integrity exceptions cannot leak as uncontrolled 500s."""
    draft_store = InMemoryDraftStore()
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=draft_store,
        strategy_store=InMemoryPublicationStore(draft_store),
    )
    monkeypatch.setattr(draft_store, operation, AsyncMock(side_effect=TypeError("driver detail")))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.request("POST" if operation == "create_draft" else "GET", path)

    assert response.status_code == 503
    assert response.json() == {"detail": detail}
