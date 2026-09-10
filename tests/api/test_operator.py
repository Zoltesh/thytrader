"""HTTP contract tests for the versioned operator diagnostics API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.operator.models import SCHEMA_VERSION
from thytrader.strategies.publication import DisabledStrategyPublicationStore

if TYPE_CHECKING:
    from thytrader.strategies.authoring import StrategyDefinition, StrategyDraft


class _RecordingDraftStore:
    """Fail closed on writes while allowing empty draft lists."""

    def __init__(self) -> None:
        """Start with zero create calls."""
        self.create_calls = 0

    async def create_draft(self, definition: StrategyDefinition) -> StrategyDraft:
        """Count forbidden mutations."""
        self.create_calls += 1
        del definition
        message = "operator routes must not create drafts"
        raise RuntimeError(message)

    async def list_drafts(self) -> tuple[StrategyDraft, ...]:
        """Return no drafts."""
        return ()

    async def save_draft(
        self,
        definition: StrategyDefinition,
        *,
        expected_revision: int,
    ) -> StrategyDraft:
        """Refuse saves."""
        del definition, expected_revision
        message = "operator routes must not save drafts"
        raise RuntimeError(message)

    async def delete_draft(self, strategy_id: object, version: int) -> None:
        """Refuse deletes."""
        del strategy_id, version
        message = "operator routes must not delete drafts"
        raise RuntimeError(message)


_OPERATOR_PATHS = (
    "/api/v1/operator/health",
    "/api/v1/operator/configuration",
    "/api/v1/operator/exchange",
    "/api/v1/operator/market-data",
    "/api/v1/operator/strategies",
    "/api/v1/operator/performance",
    "/api/v1/operator/risk",
    "/api/v1/operator/reconciliation",
    "/api/v1/operator/runtime",
    "/api/v1/operator/data-catalog",
    "/api/v1/operator/products",
    "/api/v1/operator/indicators",
    "/api/v1/operator/support-bundle",
)


def test_operator_routes_return_versioned_get_reports() -> None:
    """Every operator endpoint should be GET-only JSON with the v1 envelope."""
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        for path in _OPERATOR_PATHS:
            response = client.get(path)
            assert response.status_code == 200, path
            payload = response.json()
            assert payload["schema_version"] == SCHEMA_VERSION
            assert payload["timezone"] == "UTC"
            assert payload["overall_status"] in {"healthy", "degraded", "failed"}
            assert payload["redaction"]["secrets_redacted"] is True
            assert client.post(path).status_code in {404, 405, 422}


def test_operator_health_does_not_mutate_drafts() -> None:
    """Health diagnostics must not create or save strategy drafts."""
    drafts = _RecordingDraftStore()
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=drafts,
        strategy_store=DisabledStrategyPublicationStore(),
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/operator/health")
    assert response.status_code == 200
    assert drafts.create_calls == 0
