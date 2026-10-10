"""The API binds the CFM mirror for discretionary entry admission (ADR 0129, P1-2a)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.api.routes import discretionary_orders
from thytrader.config import Settings
from thytrader.risk.futures_collateral import bound_futures_account_store
from thytrader.trading.models import ExecutionConflictError

if TYPE_CHECKING:
    import pytest

    from thytrader.exchanges.futures_models import FuturesAccountObservation


class _Store:
    """A mirror store marker with no snapshot."""

    async def record(self, observation: FuturesAccountObservation) -> None:
        """Unused."""
        del observation

    async def latest(self) -> FuturesAccountObservation | None:
        """No snapshot."""
        return None


def test_discretionary_route_binds_the_mirror_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admission inside the route sees the app's futures account store."""
    seen: list[object] = []

    async def capture(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        seen.append(bound_futures_account_store())
        raise ExecutionConflictError("captured")

    monkeypatch.setattr(discretionary_orders, "_place", capture)
    store = _Store()
    app = create_app(Settings(_env_file=None))
    app.state.futures_account_store = store
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/discretionary-orders",
            json={
                "mode": "paper",
                "product_id": "BTC-USD",
                "stop_price": "90",
                "take_profit_price": "120",
                "origin": "agent",
                "idempotency_key": "collateral-binding",
                "quote_notional": "10",
                "limit_price": "100",
            },
        )
    assert response.status_code == 409
    assert seen == [store]
    assert bound_futures_account_store() is None
