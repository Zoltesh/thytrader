"""HTTP contract tests for mutable strategies and start-time snapshots (ADR 0082)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from thytrader.api.app import create_app
from thytrader.backtest.submission import (
    BacktestSubmissionRequest,
    BacktestSubmissionResult,
)
from thytrader.config import Settings
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import (
    create_strategy_from_definition,
    parse_document_text,
)
from thytrader.strategies.memory_store import InMemoryStrategyStore
from thytrader.strategies.models import (
    StrategyDefinition,
    canonical_strategy_bytes,
    strategy_fingerprint,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from thytrader.strategies.library import StrategyRecord

_DATASET = "sha256:" + "d" * 64


class RecordingSubmitter:
    """Record the internal fingerprint-bound request and return fixed identities."""

    def __init__(self) -> None:
        """Start with no submissions."""
        self.requests: list[BacktestSubmissionRequest] = []

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Return deterministic identities for one submission."""
        self.requests.append(request)
        return BacktestSubmissionResult(
            run_fingerprint="sha256:" + "a" * 64,
            result_fingerprint="sha256:" + "b" * 64,
        )


def _app(store: InMemoryStrategyStore, submitter: RecordingSubmitter | None = None) -> TestClient:
    """Build one app around an in-memory strategy store."""
    app = create_app(
        Settings(_env_file=None),
        strategy_store=store,
        backtest_submitter=submitter or RecordingSubmitter(),
    )
    return TestClient(app)


@pytest.fixture
def store() -> InMemoryStrategyStore:
    """Return one empty in-memory strategy store."""
    return InMemoryStrategyStore()


@pytest.fixture
def client(store: InMemoryStrategyStore) -> Iterator[TestClient]:
    """Yield one client bound to the store."""
    with _app(store) as test_client:
        yield test_client


def _seed(
    store: InMemoryStrategyStore, definition: StrategyDefinition | None = None
) -> StrategyRecord:
    """Persist one template (or supplied) definition as a strategy."""
    return asyncio.run(
        create_strategy_from_definition(store, definition or create_template_strategy())
    )


def _backtest_body(strategy_id: UUID) -> dict[str, object]:
    """Return one valid backtest start body."""
    return {
        "strategy_id": str(strategy_id),
        "dataset_fingerprint": _DATASET,
        "evaluation_start": "2026-08-01T00:00:00Z",
        "evaluation_end": "2026-08-02T00:00:00Z",
        "initial_quote_balance": "10000",
        "maker_fee_rate": "0.001",
        "taker_fee_rate": "0.002",
        "fixed_slippage_bps": "1",
    }


def test_strategy_storage_unavailable_without_a_database_fails_closed() -> None:
    """Without durable storage the library reports a controlled 503."""
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.get("/api/v1/strategies")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "strategy_storage_unavailable"


def test_create_returns_a_valid_template_strategy_without_lifecycle_fields(
    client: TestClient,
) -> None:
    """Creation returns one mutable strategy at revision 1 with its current fingerprint."""
    response = client.post("/api/v1/strategies?product_id=ETH-USDC&timeframe=5m")
    assert response.status_code == 201
    body = response.json()
    assert body["revision"] == 1
    assert body["validation"] == {"valid": True, "issues": [], "warnings": []}
    strategy = body["strategy"]
    assert "version" not in strategy
    assert "status" not in strategy
    assert strategy["instrument"]["product_id"] == "ETH-USDC"
    assert body["product_id"] == "ETH-USDC"
    assert body["timeframe"] == "5m"
    definition = StrategyDefinition.model_validate(strategy)
    assert body["current_fingerprint"] == strategy_fingerprint(definition)
    assert body["summary"]
    rejected = client.post("/api/v1/strategies?timeframe=3m")
    assert rejected.status_code == 422


def test_save_in_place_allows_invalid_work_in_progress_and_rejects_stale_revisions(
    client: TestClient,
) -> None:
    """Invalid documents save with their issues; a stale revision is a 409, not an overwrite."""
    created = client.post("/api/v1/strategies").json()
    strategy_id = created["strategy_id"]
    broken = dict(created["document"])
    broken["indicators"] = []
    saved = client.put(
        f"/api/v1/strategies/{strategy_id}", json={"document": broken, "revision": 1}
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["revision"] == 2
    assert body["validation"]["valid"] is False
    assert body["validation"]["issues"]
    assert body["strategy"] is None
    assert body["current_fingerprint"] is None

    stale = client.put(
        f"/api/v1/strategies/{strategy_id}",
        json={"document": created["document"], "revision": 1},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "strategy_revision_conflict"
    assert stale.json()["detail"]["current_revision"] == 2
    assert client.get(f"/api/v1/strategies/{strategy_id}").json()["document"]["indicators"] == []

    coerced = client.put(
        f"/api/v1/strategies/{strategy_id}",
        json={"document": created["document"], "revision": "2"},
    )
    assert coerced.status_code == 422
    not_object = client.put(
        f"/api/v1/strategies/{strategy_id}", json={"document": [1, 2], "revision": 2}
    )
    assert not_object.status_code == 422
    missing = client.put(
        f"/api/v1/strategies/{uuid4()}", json={"document": created["document"], "revision": 1}
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "strategy_not_found"


def test_save_forces_row_identity_and_drops_legacy_lifecycle_keys(client: TestClient) -> None:
    """A document cannot move itself to another identity or smuggle version/status back."""
    created = client.post("/api/v1/strategies").json()
    document = dict(created["document"])
    document["strategy_id"] = str(uuid4())
    document["version"] = 7
    document["status"] = "published"
    saved = client.put(
        f"/api/v1/strategies/{created['strategy_id']}",
        json={"document": document, "revision": 1},
    ).json()
    assert saved["strategy_id"] == created["strategy_id"]
    assert saved["document"]["strategy_id"] == created["strategy_id"]
    assert "version" not in saved["document"]
    assert "status" not in saved["document"]
    assert saved["current_fingerprint"] == created["current_fingerprint"]


def test_import_and_clone_create_new_identities(client: TestClient) -> None:
    """Import always mints a fresh identity; clone copies the document under a new one."""
    legacy = dict(create_template_strategy().model_dump(mode="json"))
    legacy["version"] = 3
    legacy["status"] = "published"
    imported = client.post("/api/v1/strategies/import", json={"document": legacy})
    assert imported.status_code == 201
    body = imported.json()
    assert body["strategy_id"] != legacy["strategy_id"]
    assert body["validation"]["valid"] is True
    again = client.post("/api/v1/strategies/import", json={"document": legacy}).json()
    assert again["strategy_id"] != body["strategy_id"]
    cloned = client.post(f"/api/v1/strategies/{body['strategy_id']}/clone")
    assert cloned.status_code == 201
    assert cloned.json()["strategy_id"] != body["strategy_id"]
    assert cloned.json()["name"].endswith("(copy)")
    invalid_import = client.post("/api/v1/strategies/import", json={"document": {"name": "WIP"}})
    assert invalid_import.status_code == 201
    assert invalid_import.json()["validation"]["valid"] is False
    assert invalid_import.json()["name"] == "WIP"


def test_backtest_start_snapshots_current_rules_and_returns_the_fingerprint(
    store: InMemoryStrategyStore,
) -> None:
    """The server snapshots strategy_id and hands the submitter the exact fingerprint."""
    record = _seed(store)
    submitter = RecordingSubmitter()
    with _app(store, submitter) as client:
        response = client.post("/api/v1/backtests", json=_backtest_body(record.strategy_id))
        queued = client.post(
            "/api/v1/backtests?async=true", json=_backtest_body(record.strategy_id)
        )
        jobs = client.get(f"/api/v1/research/jobs?strategy_id={record.strategy_id}")
    assert response.status_code == 201
    body = response.json()
    assert body["strategy_id"] == str(record.strategy_id)
    assert body["strategy_fingerprint"] == record.current_fingerprint
    assert submitter.requests[0].strategy_fingerprint == record.current_fingerprint
    assert queued.status_code == 202
    assert queued.json()["strategy_fingerprint"] == record.current_fingerprint
    assert jobs.status_code == 200
    assert jobs.json()["returned"] == 1
    assert jobs.json()["jobs"][0]["strategy_id"] == str(record.strategy_id)


def test_invalid_definition_blocks_backtest_and_deployment_starts(
    store: InMemoryStrategyStore,
) -> None:
    """Starting from an invalid saved document fails closed with strategy_invalid."""
    record = _seed(store)
    broken = dict(record.document)
    broken["indicators"] = []
    asyncio.run(store.save(record.strategy_id, broken, expected_revision=1))
    submitter = RecordingSubmitter()
    with _app(store, submitter) as client:
        backtest = client.post("/api/v1/backtests", json=_backtest_body(record.strategy_id))
        deploy = client.post(
            "/api/v1/deployments",
            json={
                "strategy_id": str(record.strategy_id),
                "mode": "paper",
                "paper_starting_cash": "1000",
            },
        )
        missing = client.post("/api/v1/backtests", json=_backtest_body(uuid4()))
    assert backtest.status_code == 422
    assert backtest.json()["detail"]["code"] == "strategy_invalid"
    assert backtest.json()["detail"]["issues"]
    assert deploy.status_code == 422
    assert deploy.json()["detail"]["code"] == "strategy_invalid"
    assert missing.status_code == 404
    assert submitter.requests == []


def test_snapshot_lookup_resolves_the_owner_and_reports_earlier_edits(
    store: InMemoryStrategyStore,
) -> None:
    """Old fingerprint links resolve their strategy and say whether rules changed since."""
    record = _seed(store)
    first = asyncio.run(store.snapshot(record.strategy_id))
    edited = dict(record.document)
    edited["name"] = "Renamed"
    asyncio.run(store.save(record.strategy_id, edited, expected_revision=1))
    with _app(store) as client:
        earlier = client.get(f"/api/v1/strategies/snapshots/{first.strategy_fingerprint}")
        unknown = client.get("/api/v1/strategies/snapshots/sha256:" + "e" * 64)
        malformed = client.get("/api/v1/strategies/snapshots/not-a-fingerprint")
    assert earlier.status_code == 200
    body = earlier.json()
    assert body["strategy_id"] == str(record.strategy_id)
    assert body["strategy_name"] == "Renamed"
    assert body["is_current"] is False
    assert body["strategy"]["name"] == record.name
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "strategy_snapshot_not_found"
    assert malformed.status_code == 404


def test_delete_and_bulk_delete_report_per_strategy_outcomes() -> None:
    """Single delete returns counts; bulk needs confirm and reports partial failures."""
    blocked_id: list[UUID] = []

    async def blocking(strategy_id: UUID) -> tuple[UUID, ...]:
        return (UUID(int=1),) if strategy_id in blocked_id else ()

    store = InMemoryStrategyStore(blocking_deployments=blocking)
    first, second, third = _seed(store), _seed(store), _seed(store)
    blocked_id.append(third.strategy_id)
    with _app(store) as client:
        deleted = client.delete(f"/api/v1/strategies/{first.strategy_id}")
        refused = client.delete(f"/api/v1/strategies/{third.strategy_id}")
        unconfirmed = client.post(
            "/api/v1/strategies/bulk-delete",
            json={"strategy_ids": [str(second.strategy_id)]},
        )
        preview = client.post(
            "/api/v1/strategies/bulk-delete",
            json={"strategy_ids": [str(second.strategy_id)], "dry_run": True},
        )
        bulk = client.post(
            "/api/v1/strategies/bulk-delete",
            json={
                "strategy_ids": [
                    str(second.strategy_id),
                    str(third.strategy_id),
                    str(first.strategy_id),
                ],
                "confirm": True,
            },
        )
        library = client.get("/api/v1/strategies").json()
    assert deleted.status_code == 200
    assert deleted.json()["outcome"] == "deleted"
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "strategy_has_active_deployments"
    assert unconfirmed.status_code == 400
    assert unconfirmed.json()["detail"]["code"] == "confirmation_required"
    assert preview.json()["results"][0]["outcome"] == "would_delete"
    assert preview.json()["would_delete"] == 1
    outcomes = [item["outcome"] for item in bulk.json()["results"]]
    assert outcomes == ["deleted", "blocked", "not_found"]
    assert bulk.json()["deleted"] == 1
    assert bulk.json()["blocked"] == 1
    assert bulk.json()["not_found"] == 1
    assert [row["strategy_id"] for row in library["strategies"]] == [str(third.strategy_id)]


def test_library_pages_with_total_and_cursor(store: InMemoryStrategyStore) -> None:
    """The library is paged newest-updated first with an opaque cursor and a total."""
    records = [_seed(store) for _ in range(3)]
    with _app(store) as client:
        first = client.get("/api/v1/strategies?limit=2").json()
        second = client.get(f"/api/v1/strategies?limit=2&cursor={first['next_cursor']}").json()
        bad = client.get("/api/v1/strategies?cursor=%%%")
    assert first["total"] == 3
    assert first["returned"] == 2
    assert first["has_more"] is True
    assert second["returned"] == 1
    assert second["has_more"] is False
    listed = {row["strategy_id"] for row in first["strategies"] + second["strategies"]}
    assert listed == {str(record.strategy_id) for record in records}
    row = first["strategies"][0]
    assert row["valid"] is True
    assert row["paper_live"] == {"paper": "none", "live": "none"}
    assert bad.status_code == 400


def test_strategy_summary_follows_crossover_operands_not_indicator_order(
    store: InMemoryStrategyStore,
) -> None:
    """Reordering declarations cannot reverse the operator-readable entry rule."""
    definition = create_template_strategy()
    _seed(
        store, definition.model_copy(update={"indicators": tuple(reversed(definition.indicators))})
    )
    with _app(store) as client:
        summary = client.get("/api/v1/strategies").json()["strategies"][0]["summary"]
    assert "EMA(20) crosses above EMA(50)" in summary


def test_strategy_summary_preserves_exact_long_decimal_risk_percentage(
    store: InMemoryStrategyStore,
) -> None:
    """Human-readable financial values do not round under ambient Decimal precision."""
    payload = create_template_strategy().model_dump(mode="python")
    payload["sizing"]["risk_fraction"] = "0.123456789012345678901234567890123456789"
    _seed(store, StrategyDefinition.model_validate(payload))
    with _app(store) as client:
        summary = client.get("/api/v1/strategies").json()["strategies"][0]["summary"]
    assert "12.3456789012345678901234567890123456789% risk" in summary


def test_strategy_summary_uses_macd_entry_rule_not_ema_fallback(
    store: InMemoryStrategyStore,
) -> None:
    """MACD template rows must not fall back to generic EMA/RSI summary text."""
    _seed(store, create_template_strategy(template="macd-trend"))
    with _app(store) as client:
        summary = client.get("/api/v1/strategies").json()["strategies"][0]["summary"]
    assert "MACD line crosses above MACD signal" in summary
    assert "EMA rules" not in summary


def test_openapi_strategy_id_uses_uuid7_format(client: TestClient) -> None:
    """The published strategy schema still advertises UUIDv7 identity."""
    schema = client.get("/openapi.json").json()
    definition = schema["components"]["schemas"]["StrategyDefinition"]
    assert definition["properties"]["strategy_id"]["format"] == "uuid7"
    assert "version" not in definition["properties"]
    assert "status" not in definition["properties"]


def test_canonical_document_round_trips_through_storage_text() -> None:
    """Stored canonical text parses back into the identical document and fingerprint."""
    definition = create_template_strategy()
    text = canonical_strategy_bytes(definition).decode("utf-8")
    document = parse_document_text(text)
    rebuilt = StrategyDefinition.model_validate(cast("dict[str, object]", document))
    assert strategy_fingerprint(rebuilt) == strategy_fingerprint(definition)
