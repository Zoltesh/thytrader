"""HTTP contract for portfolios, sleeves, journal, and portfolio backtests (ADR 0088)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient
import pytest

from tests.portfolios.fixtures import FakeChildBacktests, datasets_for_two_sleeves, strategy
from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.market_data.datasets import DatasetStore
from thytrader.portfolios.store import InMemoryPortfolioStore
from thytrader.research.jobs import ResearchExecutionMode
from thytrader.strategies.memory_store import InMemoryStrategyStore

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from thytrader.strategies.library import StrategyRecord

JsonBody = dict[str, Any]
"""A decoded TestClient JSON body (dynamic at this HTTP boundary; asserted field by field)."""

_COSTS = {"maker_fee_rate": "0.004", "taker_fee_rate": "0.006", "fixed_slippage_bps": "5"}


class Harness:
    """One app with in-memory strategies, portfolios, and fake child backtests."""

    def __init__(
        self, client: TestClient, strategies: InMemoryStrategyStore, children: FakeChildBacktests
    ) -> None:
        """Keep the pieces tests need."""
        self.client = client
        self.strategies = strategies
        self.children = children

    def create(self, **overrides: object) -> JsonBody:
        """Create one portfolio and return its body."""
        body = {
            "name": "Core",
            "mode": "paper",
            "capital_quote": "1000",
            "cash_reserve_fraction": "0.2",
        }
        response = self.client.post("/api/v1/portfolios", json={**body, **overrides})
        assert response.status_code == 201, response.text
        return response.json()


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    """Yield a running app with the portfolio runner in its lifespan."""
    strategies = InMemoryStrategyStore()
    children = FakeChildBacktests({})
    app = create_app(
        Settings(_env_file=None, market_data_dataset_root=tmp_path),
        strategy_store=strategies,
        portfolio_store=InMemoryPortfolioStore(strategies=strategies),
        backtest_submitter=children,
        backtest_result_store=children,
        dataset_store=DatasetStore(tmp_path),
        research_execution=ResearchExecutionMode.IN_PROCESS,
    )
    with TestClient(app) as client:
        yield Harness(client, strategies, children)


def _sleeve(harness: Harness, portfolio: JsonBody, record: StrategyRecord, weight: str) -> JsonBody:
    """Add one sleeve at the portfolio's current revision."""
    response = harness.client.post(
        f"/api/v1/portfolios/{portfolio['portfolio_id']}/sleeves",
        json={
            "revision": portfolio["revision"],
            "strategy_id": str(record.strategy_id),
            "weight_fraction": weight,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_storage_is_unavailable_without_a_database() -> None:
    """Without durable storage, portfolio routes fail closed with 503."""
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.get("/api/v1/portfolios")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "portfolio_storage_unavailable"


def test_crud_sleeves_weights_limits_manager_and_journal(harness: Harness) -> None:
    """The whole edit surface, each change revision-guarded and journaled."""
    btc = strategy(harness.strategies, "BTC-USDC")
    eth = strategy(harness.strategies, "ETH-USDC")
    created = harness.create(mode="live")
    assert (created["revision"], created["mode"], created["deployable"]) == (1, "live", False)
    pid = created["portfolio_id"]
    first = _sleeve(harness, created, btc, "0.5")
    second = _sleeve(harness, first, eth, "0.3")
    assert [sleeve["capital_quote"] for sleeve in second["sleeves"]] == ["500", "300"]
    assert second["allocation"]["largest_asset"]["asset"] == "BTC"

    weights = [
        {"sleeve_id": sleeve["sleeve_id"], "weight_fraction": weight}
        for sleeve, weight in zip(second["sleeves"], ("0.4", "0.4"), strict=True)
    ]
    response = harness.client.put(
        f"/api/v1/portfolios/{pid}/weights", json={"revision": 3, "weights": weights}
    )
    assert response.status_code == 200, response.text
    assert response.json()["allocation"]["allocated_fraction"] == "0.8"

    response = harness.client.patch(
        f"/api/v1/portfolios/{pid}",
        json={
            "revision": 4,
            "limits": {"max_per_asset_fraction": "0.35", "daily_loss_quote": "15"},
            "manager": {"mandate": "Grow steadily.", "permissions": {"may_rebalance": True}},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["limits"]["daily_loss_quote"] == "15"
    assert body["allocation"]["largest_asset_within_limit"] is False
    assert body["manager"]["permissions"]["max_weight_change_per_week"] == "0.1"

    journal = harness.client.get(f"/api/v1/portfolios/{pid}/journal").json()
    assert [entry["kind"] for entry in journal["entries"]] == [
        "manager_changed",
        "limits_changed",
        "weights_changed",
        "sleeve_added",
        "sleeve_added",
        "created",
    ]
    assert {entry["channel"] for entry in journal["entries"]} == {"api"}

    sleeve_id = body["sleeves"][1]["sleeve_id"]
    removed = harness.client.delete(f"/api/v1/portfolios/{pid}/sleeves/{sleeve_id}?revision=5")
    assert removed.status_code == 200, removed.text
    assert len(removed.json()["sleeves"]) == 1
    deleted = harness.client.delete(f"/api/v1/portfolios/{pid}?revision=6")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["sleeves"] == 1
    assert harness.client.get(f"/api/v1/portfolios/{pid}").status_code == 404


def test_errors_use_stable_codes(harness: Harness) -> None:
    """Conflicts, rule violations, unknown ids, and retired fields each get one code."""
    btc = strategy(harness.strategies, "BTC-USDC")
    usd = strategy(harness.strategies, "BTC-USD")
    created = harness.create()
    pid = created["portfolio_id"]
    added = _sleeve(harness, created, btc, "0.5")

    def post(body: JsonBody) -> tuple[int, JsonBody]:
        response = harness.client.post(f"/api/v1/portfolios/{pid}/sleeves", json=body)
        return response.status_code, response.json()["detail"]

    status, detail = post(
        {"revision": 1, "strategy_id": str(usd.strategy_id), "weight_fraction": "0.1"}
    )
    assert (status, detail["code"], detail["current_revision"]) == (
        409,
        "portfolio_revision_conflict",
        2,
    )
    status, detail = post(
        {"revision": 2, "strategy_id": str(btc.strategy_id), "weight_fraction": "0.1"}
    )
    assert (status, detail["code"]) == (409, "portfolio_sleeve_exists")
    status, detail = post(
        {"revision": 2, "strategy_id": str(usd.strategy_id), "weight_fraction": "0.1"}
    )
    assert (status, detail["code"]) == (422, "portfolio_sleeve_quote_mismatch")
    eth = strategy(harness.strategies, "ETH-USDC")
    status, detail = post(
        {"revision": 2, "strategy_id": str(eth.strategy_id), "weight_fraction": "0.31"}
    )
    assert (status, detail["code"]) == (422, "portfolio_allocation_exceeded")
    status, detail = post(
        {
            "revision": 2,
            "strategy_id": "01a0f000-0000-7000-8000-000000000999",
            "weight_fraction": "0.1",
        }
    )
    assert (status, detail["code"]) == (404, "strategy_not_found")
    response = harness.client.patch(
        f"/api/v1/portfolios/{pid}", json={"revision": 2, "quote_currency": "USD"}
    )
    assert response.status_code == 422
    response = harness.client.patch(
        f"/api/v1/portfolios/{pid}",
        json={"revision": 2, "manager": {"permissions": {"may_place_orders": True}}},
    )
    assert response.status_code == 422
    assert "never places orders" in response.text
    assert added["revision"] == 2


def test_backtest_runs_async_and_serves_the_combined_result(
    harness: Harness, tmp_path: Path
) -> None:
    """202, poll to completed, then the stored result (thinned on request) and listing."""
    btc = strategy(harness.strategies, "BTC-USDC", "1h")
    eth = strategy(harness.strategies, "ETH-USDC", "4h")
    harness.children.timeframes.update(
        {str(btc.current_fingerprint): "1h", str(eth.current_fingerprint): "4h"}
    )
    datasets_for_two_sleeves(tmp_path)
    created = harness.create()
    pid = created["portfolio_id"]
    _sleeve(harness, _sleeve(harness, created, btc, "0.5"), eth, "0.3")
    accepted = harness.client.post(
        f"/api/v1/portfolios/{pid}/backtests", json={**_COSTS, "revision": 3}
    )
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job"]["job_id"]
    assert [sleeve["capital_quote"] for sleeve in accepted.json()["sleeves"]] == ["500", "300"]
    job: JsonBody = {}
    for _attempt in range(100):
        job = harness.client.get(f"/api/v1/portfolios/{pid}/backtests/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert job["status"] == "completed", job
    fingerprint = job["result_fingerprint"]
    detail = harness.client.get(
        f"/api/v1/portfolios/{pid}/backtests/{fingerprint}?max_points=50"
    ).json()
    assert detail["equity_curve_downsampled"] is True
    assert len(detail["result"]["equity_curve"]) <= 50
    assert detail["result"]["contract"] == "thytrader-portfolio-backtest-v1"
    assert detail["result"]["summary"]["cash_quote"] == "200"
    listing = harness.client.get(f"/api/v1/portfolios/{pid}/backtests").json()
    assert [row["result_fingerprint"] for row in listing["entries"]] == [fingerprint]
    jobs = harness.client.get(f"/api/v1/portfolios/{pid}/backtests/jobs").json()
    assert jobs["jobs"][0]["status"] == "completed"
    missing = harness.client.get(f"/api/v1/portfolios/{pid}/backtests/sha256:{'0' * 64}")
    assert missing.json()["detail"]["code"] == "portfolio_backtest_not_found"


def test_backtest_rejections_list_each_sleeve_problem(harness: Harness) -> None:
    """No datasets: 422 portfolio_backtest_rejected with one problem per sleeve."""
    btc = strategy(harness.strategies, "BTC-USDC")
    created = harness.create()
    pid = created["portfolio_id"]
    _sleeve(harness, created, btc, "0.5")
    response = harness.client.post(f"/api/v1/portfolios/{pid}/backtests", json=_COSTS)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "portfolio_backtest_rejected"
    assert detail["problems"][0]["code"] == "dataset_missing"
    empty = harness.create(name="Empty")
    response = harness.client.post(
        f"/api/v1/portfolios/{empty['portfolio_id']}/backtests", json=_COSTS
    )
    assert response.json()["detail"]["code"] == "portfolio_backtest_rejected"
