"""HTTP deployment starts for strategies with reference instruments (ADR 0096).

``POST /api/v1/deployments`` refuses (409) a strategy whose reference series is not on
the enabled market-data watchlist, naming the ``thytrader-data watch-add`` command, and
starts it once the series is watched. Reference-free strategies are unaffected.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from tests.strategies.reference_support import reference_strategy
from tests.strategy_fakes import SeededStrategyStore
from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.market_data.models import CandleInterval
from thytrader.market_data.watchlist import InMemoryMarketDataWatchlistStore, MarketDataWatchTarget
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.snapshots import StrategySnapshot
from thytrader.trading.memory import InMemoryExecutionStore


def _client(
    definition: StrategyDefinition, watchlist: InMemoryMarketDataWatchlistStore
) -> tuple[TestClient, InMemoryExecutionStore]:
    """An API client with the strategy published and the given watchlist."""
    publication = SeededStrategyStore()
    fingerprint = strategy_fingerprint(definition)
    publication.published[fingerprint] = StrategySnapshot(
        strategy_fingerprint=fingerprint, definition=definition
    )
    execution = InMemoryExecutionStore()
    app = create_app(
        Settings(_env_file=None),
        strategy_store=publication,
        execution_store=execution,
        market_data_watchlist_store=watchlist,
    )
    return TestClient(app), execution


def _payload(definition: StrategyDefinition) -> dict[str, str]:
    """A paper start body."""
    return {
        "strategy_id": str(definition.strategy_id),
        "mode": "paper",
        "paper_starting_cash": "10000",
    }


def test_unwatched_reference_series_refuses_the_start_with_the_watch_command() -> None:
    """409 names the reference series and the exact data-lane command; nothing starts."""
    definition = reference_strategy()
    client, execution = _client(definition, InMemoryMarketDataWatchlistStore())
    with client:
        response = client.post("/api/v1/deployments", json=_payload(definition))
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "btc (BTC-USD 1d)" in detail
    assert "uv run thytrader-data watch-add --product-id BTC-USD --timeframe 1d" in detail
    assert asyncio.run(execution.list_deployments()) == ()


def test_watched_reference_series_starts_the_bot() -> None:
    """With BTC-USD 1d watched (demo provider without credentials), the start succeeds."""
    definition = reference_strategy()
    watchlist = InMemoryMarketDataWatchlistStore()
    asyncio.run(
        watchlist.upsert(
            MarketDataWatchTarget(
                provider="demo",
                product_id="BTC-USD",
                timeframe=CandleInterval.ONE_DAY,
                lookback_hours=2424,
                enabled=True,
                updated_at=datetime(2026, 10, 1, tzinfo=UTC),
            )
        )
    )
    client, _execution = _client(definition, watchlist)
    with client:
        response = client.post("/api/v1/deployments", json=_payload(definition))
    assert response.status_code == 201
    assert response.json()["instrument_runtimes"][0]["product_id"] == "ETH-USD"
