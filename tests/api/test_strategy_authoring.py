"""Behavioral tests for browser-facing strategy authoring contracts."""

from __future__ import annotations

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.backtest.submission import (
    BacktestSubmissionRequest,
    BacktestSubmissionResult,
)
from thytrader.config import Settings
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.publication import PublishedStrategy


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

    def __init__(self) -> None:
        """Start without any published definition."""
        self.published: StrategyDefinition | None = None

    async def publish(self, definition: StrategyDefinition) -> PublishedStrategy:
        """Return the exact immutable definition under its canonical fingerprint."""
        self.published = definition
        return PublishedStrategy(
            strategy_fingerprint=strategy_fingerprint(definition), definition=definition
        )


def test_strategy_creation_returns_an_ephemeral_conservative_draft() -> None:
    """Creating a browser draft supplies server-owned identity and safe reference defaults."""
    app = create_app(Settings(_env_file=None))

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
    store = InMemoryStrategyPublicationStore()
    app = create_app(Settings(_env_file=None), strategy_store=store)

    with TestClient(app) as client:
        draft_response = client.post("/api/v1/strategies")
        draft = draft_response.json()["strategy"]
        response = client.post(
            f"/api/v1/strategies/{draft['strategy_id']}/publish",
            json={"strategy": draft},
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
