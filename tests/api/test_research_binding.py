"""HTTP contracts for omitted research datasets, study windows, and market variants (ADR 0089)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.backtest.models import BacktestResult, BacktestSummary, EquityPoint
from thytrader.backtest.submission import BacktestSubmissionRequest, BacktestSubmissionResult
from thytrader.config import Settings
from thytrader.market_data.datasets import DatasetManifest, DatasetStore, DatasetStoreError
from thytrader.research.catalog import InMemoryResearchStudyCatalog
from thytrader.research.jobs import ResearchExecutionMode
from thytrader.strategies.memory_store import InMemoryStrategyStore

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from thytrader.persistence.backtest_results import BacktestResultSummaryView

_STARTS_AT = "2026-01-01T00:00:00Z"
_ENDS_AT = "2026-03-01T00:00:00Z"
_COSTS = {
    "initial_quote_balance": "10000",
    "maker_fee_rate": "0.004",
    "taker_fee_rate": "0.006",
    "fixed_slippage_bps": "5",
}


def _fingerprint(product_id: str, timeframe: str, provider: str = "demo") -> str:
    """Return a deterministic dataset identity for one cataloged clock."""
    return "sha256:" + sha256(f"{provider}|{product_id}|{timeframe}".encode()).hexdigest()


def _manifest(
    product_id: str, timeframe: str = "1h", *, provider: str = "demo", starts_at: str = _STARTS_AT
) -> DatasetManifest:
    """Return one complete catalog row covering the test window."""
    return DatasetManifest(
        provider=provider,
        product_id=product_id,
        timeframe=timeframe,
        starts_at=starts_at,
        ends_at=_ENDS_AT,
        expected_candle_count=1,
        received_candle_count=1,
        gap_count=0,
        missing_intervals=0,
        complete=True,
        content_fingerprint=_fingerprint(product_id, timeframe, provider),
        files=(Path("unused.parquet"),),
        manifest_path=Path("unused.json"),
    )


class _CatalogStore(DatasetStore):
    """Serve canned catalog rows and manifests without reading Parquet."""

    def __init__(self, *manifests: DatasetManifest) -> None:
        """Index the rows by fingerprint."""
        super().__init__(Path("unused-datasets"))
        self._rows = manifests
        self._by_fingerprint = {row.content_fingerprint: row for row in manifests}

    def list_latest_verified(self) -> tuple[DatasetManifest, ...]:
        """Return the canned newest revisions."""
        return self._rows

    def load_manifest(self, content_fingerprint: str) -> DatasetManifest:
        """Return one canned manifest or fail like a missing artifact."""
        try:
            return self._by_fingerprint[content_fingerprint]
        except KeyError as error:
            raise DatasetStoreError("missing dataset") from error


class _RecordingSubmitter:
    """Record child submissions and return distinct identities."""

    def __init__(self) -> None:
        """Start with no submissions."""
        self.requests: list[BacktestSubmissionRequest] = []

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Remember the exact request without simulating."""
        self.requests.append(request)
        suffix = str(len(self.requests))
        return BacktestSubmissionResult(
            run_fingerprint="sha256:" + suffix.ljust(64, "c"),
            result_fingerprint="sha256:" + suffix.ljust(64, "d"),
        )


class _Results:
    """Return one constant result for every child."""

    async def list_summaries(
        self,
        *,
        run_fingerprint: str | None = None,
        strategy_fingerprint: str | None = None,
        dataset_fingerprint: str | None = None,
        strategy_id: UUID | None = None,
        limit: int,
        offset: int,
    ) -> tuple[BacktestResultSummaryView, ...]:
        """Unused by these routes."""
        del run_fingerprint, strategy_fingerprint, dataset_fingerprint, strategy_id
        del limit, offset
        return ()

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Return a flat child result."""
        fingerprint = "sha256:" + "e" * 64
        del result_fingerprint
        return BacktestResult(
            schema_version="1.0",
            run_fingerprint=fingerprint,
            strategy_fingerprint=fingerprint,
            dataset_fingerprint=fingerprint,
            signal_trace_fingerprint=fingerprint,
            trades=(),
            equity_curve=(
                EquityPoint(
                    candle_starts_at=datetime(2026, 1, 1, tzinfo=UTC),
                    cash="10000",
                    base_quantity="0",
                    mark_price="1",
                    equity="10000",
                ),
            ),
            summary=BacktestSummary(
                initial_equity="10000",
                final_equity="10000",
                total_net_pnl="0",
                total_return_fraction="0",
                gross_profit="0",
                gross_loss="0",
                win_rate="0",
                trade_count=0,
                winning_trade_count=0,
                maximum_drawdown="0",
                maximum_drawdown_fraction="0",
                exposure_bars=0,
                evaluation_bars=10,
            ),
        )

    async def list_summaries_for_strategies(
        self, strategy_fingerprints: Sequence[str]
    ) -> dict[str, BacktestResultSummaryView]:
        """Return no summaries."""
        del strategy_fingerprints
        return {}


def _client(
    store: DatasetStore,
) -> tuple[TestClient, InMemoryStrategyStore, _RecordingSubmitter]:
    """Build an API on in-memory research stores and one canned dataset catalog."""
    strategies = InMemoryStrategyStore()
    submitter = _RecordingSubmitter()
    app = create_app(
        Settings(_env_file=None),
        strategy_store=strategies,
        backtest_submitter=submitter,
        backtest_result_store=_Results(),
        research_study_catalog=InMemoryResearchStudyCatalog(),
        dataset_store=store,
        research_execution=ResearchExecutionMode.IN_PROCESS,
    )
    return TestClient(app), strategies, submitter


def _create_strategy(client: TestClient, product_id: str = "BTC-USD") -> str:
    """Create one EMA template strategy on ``product_id`` 1h and return its id."""
    created = client.post(f"/api/v1/strategies?product_id={product_id}&timeframe=1h")
    assert created.status_code == 201, created.text
    strategy_id = created.json()["strategy_id"]
    assert isinstance(strategy_id, str)
    return strategy_id


def test_backtest_without_a_dataset_binds_the_latest_catalog_dataset() -> None:
    """Omitting dataset_fingerprint binds the newest complete dataset and echoes it."""
    store = _CatalogStore(_manifest("BTC-USD"), _manifest("BTC-USD", provider="coinbase"))
    client, _, submitter = _client(store)
    with client:
        strategy_id = _create_strategy(client)
        response = client.post("/api/v1/backtests", json={"strategy_id": strategy_id, **_COSTS})
    assert response.status_code == 201, response.text
    assert response.json()["bound_datasets"] == [
        {
            "product_id": "BTC-USD",
            "timeframe": "1h",
            "role": "decision",
            "dataset_fingerprint": _fingerprint("BTC-USD", "1h"),
            "source": "latest_catalog",
        }
    ]
    [request] = submitter.requests
    assert request.dataset_fingerprint == _fingerprint("BTC-USD", "1h")


def test_async_backtest_echoes_its_bound_datasets() -> None:
    """The 202 body names the datasets the queued job is bound to."""
    client, _, _ = _client(_CatalogStore(_manifest("BTC-USD")))
    with client:
        strategy_id = _create_strategy(client)
        response = client.post(
            "/api/v1/backtests?async=true", json={"strategy_id": strategy_id, **_COSTS}
        )
    assert response.status_code == 202, response.text
    body = response.json()
    assert [item["dataset_fingerprint"] for item in body["bound_datasets"]] == [
        _fingerprint("BTC-USD", "1h")
    ]
    assert body["evaluation_start"] is None


def test_backtest_without_a_cataloged_dataset_fails_closed_with_the_fix() -> None:
    """No complete dataset for the clock is a 422 listing it and the commands to fix it."""
    client, _, submitter = _client(_CatalogStore(_manifest("ETH-USD")))
    with client:
        strategy_id = _create_strategy(client)
        response = client.post("/api/v1/backtests", json={"strategy_id": strategy_id, **_COSTS})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "datasets_missing"
    assert detail["missing"] == [{"product_id": "BTC-USD", "timeframe": "1h", "role": "decision"}]
    assert (
        "thytrader-data watch-add --product-id BTC-USD --timeframe 1h --confirm"
        in (detail["message"])
    )
    assert submitter.requests == []


def test_explicit_backtest_dataset_is_used_as_sent() -> None:
    """Explicit fingerprints keep working and are echoed as request-sourced."""
    explicit = _fingerprint("BTC-USD", "1h", provider="coinbase")
    client, _, submitter = _client(_CatalogStore())
    with client:
        strategy_id = _create_strategy(client)
        response = client.post(
            "/api/v1/backtests",
            json={"strategy_id": strategy_id, "dataset_fingerprint": explicit, **_COSTS},
        )
    assert response.status_code == 201, response.text
    assert response.json()["bound_datasets"][0]["source"] == "request"
    assert submitter.requests[0].dataset_fingerprint == explicit


def _holdout(strategy_id: str) -> dict[str, object]:
    """Return an OOS holdout body with no datasets and no evaluation bounds."""
    return {"kind": "oos_holdout", "strategy_id": strategy_id, "oos_fraction": "0.3", **_COSTS}


def test_study_without_datasets_or_bounds_uses_the_common_covered_window() -> None:
    """A study may omit datasets and bounds; the plan echoes the exact bindings it used."""
    client, _, _ = _client(_CatalogStore(_manifest("BTC-USD")))
    with client:
        strategy_id = _create_strategy(client)
        response = client.post("/api/v1/research/studies/plan", json=_holdout(strategy_id))
        backtest = client.post(
            "/api/v1/backtests",
            json={"strategy_id": strategy_id, **_COSTS},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bound_datasets"][0]["dataset_fingerprint"] == _fingerprint("BTC-USD", "1h")
    starts_at = datetime.fromisoformat(body["evaluation_start"])
    ends_at = datetime.fromisoformat(body["evaluation_end"])
    assert datetime(2026, 1, 1, tzinfo=UTC) < starts_at < ends_at
    assert ends_at <= datetime(2026, 3, 1, tzinfo=UTC)
    assert backtest.status_code == 201, backtest.text


def test_study_with_one_bound_is_rejected() -> None:
    """Bounds are both omitted or both set."""
    client, _, _ = _client(_CatalogStore(_manifest("BTC-USD")))
    with client:
        strategy_id = _create_strategy(client)
        response = client.post(
            "/api/v1/research/studies/plan",
            json={**_holdout(strategy_id), "evaluation_start": "2026-01-10T00:00:00Z"},
        )
    assert response.status_code == 422
    assert "both be omitted or both set" in response.text


def test_study_without_a_cataloged_dataset_lists_the_missing_clock() -> None:
    """Studies fail closed exactly like backtests when a clock has no dataset."""
    client, _, _ = _client(_CatalogStore())
    with client:
        strategy_id = _create_strategy(client)
        response = client.post("/api/v1/research/studies/plan", json=_holdout(strategy_id))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "datasets_missing"


def _cross_market(strategy_id: str, *products: str) -> dict[str, object]:
    """Return a cross-market body naming one base strategy plus per-market products."""
    return {
        "kind": "cross_market",
        "strategy_id": strategy_id,
        "markets": [{"product_id": product} for product in products],
        **_COSTS,
    }


def test_cross_market_derives_per_market_variants_of_one_strategy() -> None:
    """One strategy_id plus markets[].product_id yields exact variants under the base id."""
    store = _CatalogStore(_manifest("BTC-USD"), _manifest("ETH-USD"), _manifest("SOL-USD"))
    client, strategies, submitter = _client(store)
    with client:
        strategy_id = _create_strategy(client)
        base_fingerprint = client.get(f"/api/v1/strategies/{strategy_id}").json()[
            "current_fingerprint"
        ]
        plan = client.post(
            "/api/v1/research/studies/plan?detail=full",
            json=_cross_market(strategy_id, "BTC-USD", "ETH-USD", "SOL-USD"),
        )
        submitted = client.post(
            "/api/v1/research/studies",
            json=_cross_market(strategy_id, "BTC-USD", "ETH-USD", "SOL-USD"),
        )
    assert plan.status_code == 200, plan.text
    windows = plan.json()["windows"]
    assert [window["product_id"] for window in windows] == ["BTC-USD", "ETH-USD", "SOL-USD"]
    fingerprints = [window["strategy_fingerprint"] for window in windows]
    assert fingerprints[0] == base_fingerprint
    assert len(set(fingerprints)) == 3
    assert [window["dataset_fingerprint"] for window in windows] == [
        _fingerprint(product, "1h") for product in ("BTC-USD", "ETH-USD", "SOL-USD")
    ]
    assert submitted.status_code == 201, submitted.text
    assert [request.strategy_fingerprint for request in submitter.requests] == fingerprints
    assert len(submitted.json()["bound_datasets"]) == 3

    async def owners() -> list[tuple[str | None, str]]:
        lookups = [await strategies.lookup_snapshot(item) for item in fingerprints[1:]]
        return [
            (
                None if lookup.strategy_id is None else str(lookup.strategy_id),
                lookup.snapshot.definition.instrument.product_id,
            )
            for lookup in lookups
        ]

    assert asyncio.run(owners()) == [(strategy_id, "ETH-USD"), (strategy_id, "SOL-USD")]


def test_cross_market_window_is_the_intersection_of_every_market() -> None:
    """A market whose history starts later moves the common evaluation start for all."""
    store = _CatalogStore(
        _manifest("BTC-USD"), _manifest("ETH-USD", starts_at="2026-02-01T00:00:00Z")
    )
    client, _, _ = _client(store)
    with client:
        strategy_id = _create_strategy(client)
        plan = client.post(
            "/api/v1/research/studies/plan?detail=full",
            json=_cross_market(strategy_id, "BTC-USD", "ETH-USD"),
        )
    assert plan.status_code == 200, plan.text
    body = plan.json()
    starts_at = datetime.fromisoformat(body["evaluation_start"])
    assert starts_at > datetime(2026, 2, 1, tzinfo=UTC)
    assert {window["evaluation_start"] for window in body["windows"]} == {body["evaluation_start"]}


def test_cross_market_product_form_requires_a_base_strategy() -> None:
    """markets[].product_id without a top-level strategy_id is a validation error."""
    client, _, _ = _client(_CatalogStore(_manifest("BTC-USD")))
    with client:
        response = client.post(
            "/api/v1/research/studies/plan",
            json={
                "kind": "cross_market",
                "markets": [{"product_id": "BTC-USD"}, {"product_id": "ETH-USD"}],
                **_COSTS,
            },
        )
    assert response.status_code == 422
    assert "requires a top-level strategy_id" in response.text


def _sweep(strategy_id: str, fast: int, slow: int) -> dict[str, object]:
    """Return a fast x slow EMA period sweep on the omitted-dataset path."""
    return {
        "kind": "parameter_sweep",
        "strategy_id": strategy_id,
        "parameter_axes": [
            {
                "indicator_id": "fast",
                "parameter": "period",
                "values": [str(5 + value) for value in range(fast)],
            },
            {
                "indicator_id": "slow",
                "parameter": "period",
                "values": [str(30 + value) for value in range(slow)],
            },
        ],
        **_COSTS,
    }


def test_large_sweeps_run_async_while_sync_stays_small() -> None:
    """A 3 x 3 grid queues as an async job (202) but is refused synchronously (422)."""
    client, _, submitter = _client(_CatalogStore(_manifest("BTC-USD")))
    with client:
        strategy_id = _create_strategy(client)
        sync = client.post("/api/v1/research/studies", json=_sweep(strategy_id, 3, 3))
        queued = client.post("/api/v1/research/studies?async=true", json=_sweep(strategy_id, 3, 3))
        oversized = client.post(
            "/api/v1/research/studies?async=true", json=_sweep(strategy_id, 8, 8)
        )
    assert sync.status_code == 422, sync.text
    assert sync.json()["detail"]["code"] == "study_budget_exceeded", sync.text
    assert "--async" in sync.json()["detail"]["message"]
    assert queued.status_code == 202, queued.text
    assert queued.json()["bound_datasets"][0]["source"] == "latest_catalog"
    assert queued.json()["evaluation_start"] is not None
    assert oversized.status_code == 202, oversized.text
    assert all(len(request.dataset_fingerprint) == 71 for request in submitter.requests)


def test_reference_template_backtest_binds_and_echoes_the_reference_dataset() -> None:
    """btc-regime-gate on ETH-USD binds BTC-USD 1d as a reference row (ADR 0096)."""
    store = _CatalogStore(_manifest("ETH-USD"), _manifest("BTC-USD", "1d"))
    client, _, submitter = _client(store)
    with client:
        created = client.post(
            "/api/v1/strategies?product_id=ETH-USD&timeframe=1h&template=btc-regime-gate"
        )
        assert created.status_code == 201, created.text
        strategy_id = created.json()["strategy_id"]
        response = client.post("/api/v1/backtests", json={"strategy_id": strategy_id, **_COSTS})
    assert response.status_code == 201, response.text
    rows = response.json()["bound_datasets"]
    assert {
        "product_id": "BTC-USD",
        "timeframe": "1d",
        "role": "reference",
        "dataset_fingerprint": _fingerprint("BTC-USD", "1d"),
        "source": "latest_catalog",
        "reference_id": "btc",
    } in rows
    assert all("reference_id" not in row for row in rows if row["role"] != "reference")
    [request] = submitter.requests
    (reference,) = request.reference_dataset_fingerprints
    assert (reference.reference_id, reference.product_id, reference.timeframe) == (
        "btc",
        "BTC-USD",
        "1d",
    )


def test_reference_backtest_without_a_cataloged_reference_names_the_series() -> None:
    """A missing BTC-USD 1d dataset is a 422 naming the reference instrument."""
    client, _, _ = _client(_CatalogStore(_manifest("ETH-USD")))
    with client:
        created = client.post(
            "/api/v1/strategies?product_id=ETH-USD&timeframe=1h&template=btc-regime-gate"
        )
        strategy_id = created.json()["strategy_id"]
        response = client.post("/api/v1/backtests", json={"strategy_id": strategy_id, **_COSTS})
    assert response.status_code == 422, response.text
    assert "BTC-USD 1d (reference instrument)" in response.text
