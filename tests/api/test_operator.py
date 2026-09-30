"""HTTP contract tests for the versioned operator diagnostics API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.operator.models import SCHEMA_VERSION
from thytrader.strategies.memory_store import InMemoryStrategyStore

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.strategies.library import StrategyDocument, StrategyRecord


class _RecordingStrategyStore(InMemoryStrategyStore):
    """Fail closed on writes while allowing empty strategy lists."""

    def __init__(self) -> None:
        """Start with zero mutation calls."""
        super().__init__()
        self.create_calls = 0

    async def create(
        self, document: StrategyDocument, *, strategy_id: UUID, created_at: datetime
    ) -> StrategyRecord:
        """Count forbidden mutations."""
        del document, strategy_id, created_at
        self.create_calls += 1
        message = "operator routes must not create strategies"
        raise RuntimeError(message)

    async def save(
        self, strategy_id: UUID, document: StrategyDocument, *, expected_revision: int
    ) -> StrategyRecord:
        """Count forbidden saves."""
        del strategy_id, document, expected_revision
        self.create_calls += 1
        message = "operator routes must not save strategies"
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
    "/api/v1/operator/monitor",
    "/api/v1/operator/trade-reasons",
    "/api/v1/operator/data-catalog",
    "/api/v1/operator/products",
    "/api/v1/operator/indicators",
    "/api/v1/operator/support-bundle",
    "/api/v1/operator/portfolio",
    "/api/v1/operator/fees",
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


def test_operator_risk_reports_available_registry() -> None:
    """Operator risk must advertise the compiled registry without balances."""
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        response = client.get("/api/v1/operator/risk")
    assert response.status_code == 200
    payload = response.json()["payload"]
    assert payload["risk_policy_registry"] == "available"
    assert payload["policy_source"] == "compiled_default"
    assert payload["daily_loss_limit_fraction"] == "1"
    assert payload["max_entry_orders_per_minute"] == 60
    assert payload["reference_price_collar_fraction"] == "0.5"
    assert payload["allow_intra_strategy_pyramiding"] is False
    assert "paper_capital_quote" not in payload


def test_operator_health_does_not_mutate_drafts() -> None:
    """Health diagnostics must not create or save strategy drafts."""
    drafts = _RecordingStrategyStore()
    app = create_app(Settings(_env_file=None), strategy_store=drafts)
    with TestClient(app) as client:
        response = client.get("/api/v1/operator/health")
    assert response.status_code == 200
    assert drafts.create_calls == 0
