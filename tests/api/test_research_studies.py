"""HTTP tests for Phase 11 templates, the backtest model, and research studies."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import json
from pathlib import Path
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.api.routes.research_studies import _study_service
from thytrader.backtest.models import BacktestResult, BacktestSummary, EquityPoint
from thytrader.backtest.submission import (
    BacktestSubmissionRequest,
    BacktestSubmissionResult,
)
from thytrader.config import Settings
from thytrader.market_data.datasets import DatasetManifest, DatasetStore
from thytrader.research.catalog import InMemoryResearchStudyCatalog
from thytrader.research.http import find_study_by_request
from thytrader.research.jobs import ResearchExecutionMode
from thytrader.research.studies import (
    ASYNC_STUDY_BUDGET,
    ResearchStudyPlan,
    ResearchStudyRequest,
    ResearchStudyService,
    StudyBudget,
)
from thytrader.strategies.memory_store import InMemoryStrategyStore

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from thytrader.persistence.backtest_results import BacktestResultSummaryView


def _summary() -> BacktestSummary:
    """Return a constant child-window summary."""
    return BacktestSummary(
        initial_equity="10000",
        final_equity="10100",
        total_net_pnl="100",
        total_return_fraction="0.01",
        gross_profit="100",
        gross_loss="0",
        win_rate="1",
        trade_count=1,
        winning_trade_count=1,
        maximum_drawdown="0",
        maximum_drawdown_fraction="0",
        exposure_bars=10,
        evaluation_bars=10,
    )


def _result_with_summary() -> BacktestResult:
    """Build a unified-model result document for study child loads."""
    fingerprint = "sha256:" + "e" * 64
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
        summary=_summary(),
    )


class _StudySubmitter:
    """Return a distinct identity pair per child window."""

    def __init__(self) -> None:
        """Start with no submissions."""
        self.calls = 0

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Count submissions without simulating fills."""
        del request
        self.calls += 1
        suffix = str(self.calls)
        return BacktestSubmissionResult(
            run_fingerprint="sha256:" + suffix.ljust(64, "c"),
            result_fingerprint="sha256:" + suffix.ljust(64, "d"),
        )


class _StudyResults:
    """Return a constant summary for every child result fingerprint."""

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
        """Unused by study routes."""
        del strategy_id
        del run_fingerprint, strategy_fingerprint, dataset_fingerprint, limit, offset
        return ()

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Return a unified-model child result with a constant summary."""
        del result_fingerprint
        return _result_with_summary()

    async def list_summaries_for_strategies(
        self,
        strategy_fingerprints: Sequence[str],
    ) -> dict[str, BacktestResultSummaryView]:
        """Return no summaries, like an empty durable catalog."""
        del strategy_fingerprints
        return {}


class _CoveringDatasetStore(DatasetStore):
    """Return a complete manifest covering the requested evaluation window."""

    def __init__(
        self,
        *,
        starts_at: str = "2020-01-01T00:00:00Z",
        ends_at: str = "2027-01-01T00:00:00Z",
    ) -> None:
        """Remember coverage bounds without writing files."""
        super().__init__(Path("unused-datasets"))
        self._starts_at = starts_at
        self._ends_at = ends_at

    def load_manifest(self, content_fingerprint: str) -> DatasetManifest:
        """Return a verified-complete stand-in for the requested fingerprint."""
        return DatasetManifest(
            provider="coinbase",
            product_id="BTC-USD",
            timeframe="1h",
            starts_at=self._starts_at,
            ends_at=self._ends_at,
            expected_candle_count=1,
            received_candle_count=1,
            gap_count=0,
            missing_intervals=0,
            complete=True,
            content_fingerprint=content_fingerprint,
            files=(Path("unused.parquet"),),
            manifest_path=Path("unused.json"),
        )


def _client(
    dataset_store: DatasetStore | None = None,
) -> tuple[TestClient, InMemoryStrategyStore, _StudySubmitter]:
    """Build an API client with in-memory research stores."""
    publications = InMemoryStrategyStore()
    submitter = _StudySubmitter()
    app = create_app(
        Settings(_env_file=None),
        strategy_store=publications,
        backtest_submitter=submitter,
        backtest_result_store=_StudyResults(),
        research_study_catalog=InMemoryResearchStudyCatalog(),
        dataset_store=dataset_store or _CoveringDatasetStore(),
        research_execution=ResearchExecutionMode.IN_PROCESS,
    )
    return TestClient(app), publications, submitter


def _publish_reference(client: TestClient) -> str:
    """Create the default EMA strategy, returning its strategy_id."""
    created = client.post("/api/v1/strategies")
    assert created.status_code == 201, created.text
    strategy_id = created.json()["strategy_id"]
    assert isinstance(strategy_id, str)
    return strategy_id


def _holdout_body(strategy_fingerprint_value: str) -> dict[str, object]:
    """Return a valid OOS holdout JSON body."""
    return {
        "kind": "oos_holdout",
        "evaluation_start": "2026-01-01T00:00:00Z",
        "evaluation_end": "2026-01-11T00:00:00Z",
        "initial_quote_balance": "10000",
        "maker_fee_rate": "0.001",
        "taker_fee_rate": "0.002",
        "fixed_slippage_bps": "10",
        "strategy_id": strategy_fingerprint_value,
        "dataset_fingerprint": "sha256:" + "b" * 64,
        "oos_fraction": "0.3",
    }


def test_backtest_model_is_a_first_class_read_contract() -> None:
    """The single model's description replaces the retired per-engine matrix."""
    client, _, _ = _client()
    with client:
        response = client.get("/api/v1/research/backtest-model")
        removed = client.get("/api/v1/research/engine-support")
    assert response.status_code == 200, response.text
    assert response.json()["engine"] == "thytrader-backtest"
    assert removed.status_code == 404


def test_study_plan_rejects_the_removed_engine_selector() -> None:
    """A study body that still selects an engine gets an explicit 422 migration message."""
    client, _, _ = _client()
    with client:
        fingerprint = _publish_reference(client)
        response = client.post(
            "/api/v1/research/studies/plan",
            json={**_holdout_body(fingerprint), "engine_contract_version": "thytrader-backtest"},
        )
    assert response.status_code == 422
    assert "engine_contract_version was removed" in response.text


def test_templates_catalog_lists_fail_closed_ids() -> None:
    """Agents can discover every research draft template."""
    client, _, _ = _client()
    with client:
        response = client.get("/api/v1/research/templates")
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["templates"]}
    assert ids == {
        "ema-trend",
        "rsi-mean-reversion",
        "macd-trend",
        "bollinger-mean-reversion",
        "donchian-breakout",
        "supertrend-trend",
        "squeeze-breakout",
        "zscore-mean-reversion",
        "ema-trend-hold",
        "btc-regime-gate",
    }


def test_ema_trend_hold_template_detail_names_its_signal_exit() -> None:
    """Agents discover the trend-hold exit rule and its axes from the blueprint (ADR 0093)."""
    client, _, _ = _client()
    with client:
        response = client.get("/api/v1/research/templates/ema-trend-hold")
    assert response.status_code == 200, response.text
    template = response.json()["template"]
    assert template["indicator_ids"] == ["fast", "slow", "atr"]
    assert template["defaults"]["exits.signal_exit"] == "fast crosses below slow"
    assert template["defaults"]["exits.take_profit"] == "none"
    parameters = {axis.get("parameter") for axis in template["sweepable_axes"]}
    assert {"period", "trailing_stop_multiple", "initial_stop_multiple"} <= parameters


def test_template_detail_names_indicator_ids_defaults_and_sweepable_axes() -> None:
    """MACD sweeps can be authored from the blueprint without reading source."""
    client, _, _ = _client()
    with client:
        response = client.get("/api/v1/research/templates/macd-trend")
        missing = client.get("/api/v1/research/templates/stochastic")
    assert response.status_code == 200, response.text
    template = response.json()["template"]
    assert template["id"] == "macd-trend"
    assert "macd" in template["indicator_ids"]
    assert template["defaults"]["macd.fast_period"] == "12"
    axes = template["sweepable_axes"]
    assert {"indicator_id": "macd", "parameter": "fast_period"} in [
        {"indicator_id": axis.get("indicator_id"), "parameter": axis.get("parameter")}
        for axis in axes
    ]
    assert missing.status_code == 404


def test_create_draft_accepts_rsi_template_and_rejects_unknown() -> None:
    """Template query selects a named draft and fails closed on unknown ids."""
    client, _, _ = _client()
    with client:
        created = client.post("/api/v1/strategies?template=rsi-mean-reversion")
        rejected = client.post("/api/v1/strategies?template=stochastic")
    assert created.status_code == 201, created.text
    assert "RSI mean reversion" in created.json()["strategy"]["name"]
    assert rejected.status_code == 400


def test_plan_study_returns_in_sample_and_out_of_sample_windows() -> None:
    """Planning does not submit child backtests."""
    client, publications, submitter = _client()
    with client:
        fingerprint = _publish_reference(client)
        summary = client.post("/api/v1/research/studies/plan", json=_holdout_body(fingerprint))
        full = client.post(
            "/api/v1/research/studies/plan?detail=full",
            json=_holdout_body(fingerprint),
        )
    assert summary.status_code == 200, summary.text
    summary_body = summary.json()
    assert summary_body["window_count"] == 2
    assert "windows" not in summary_body
    assert full.status_code == 200, full.text
    windows = full.json()["windows"]
    assert [window["role"] for window in windows] == ["in_sample", "out_of_sample"]
    assert submitter.calls == 0
    assert len(publications.snapshot_fingerprints()) == 1


def test_submit_study_composes_child_backtests() -> None:
    """Submitting a holdout study creates two child identities and OOS aggregates."""
    client, _, submitter = _client()
    with client:
        fingerprint = _publish_reference(client)
        response = client.post("/api/v1/research/studies", json=_holdout_body(fingerprint))
        assert response.status_code == 201, response.text
        body = response.json()
        assert submitter.calls == 2
        assert body["kind"] == "oos_holdout"
        assert body["aggregate"]["oos_window_count"] == 1
        assert body["study_fingerprint"].startswith("sha256:")
        listed = client.get("/api/v1/research/studies")
        assert listed.status_code == 200, listed.text
        rows = listed.json()["studies"]
        assert len(rows) == 1
        assert rows[0]["study_fingerprint"] == body["study_fingerprint"]
        assert rows[0]["kind"] == "oos_holdout"
        shown = client.get(f"/api/v1/research/studies/{body['study_fingerprint']}")
        assert shown.status_code == 200, shown.text
        summary = shown.json()
        assert summary["study_fingerprint"] == body["study_fingerprint"]
        assert "windows" not in summary
        assert summary["window_count"] == len(body["windows"])
        headlines = summary["window_pnl"]
        assert len(headlines) == len(body["windows"])
        assert {row["role"] for row in headlines} == {"in_sample", "out_of_sample"}
        assert all("total_net_pnl" in row for row in headlines)
        assert all("result_fingerprint" in row for row in headlines)
        windows = {window["label"]: window for window in body["windows"]}
        for row in headlines:
            assert row["evaluation_start"] == windows[row["label"]]["evaluation_start"]
            assert row["evaluation_end"] == windows[row["label"]]["evaluation_end"]
            assert row["product_id"] == windows[row["label"]]["product_id"]
            assert row["axis_values"] == {}
        (candidate,) = summary["candidates"]
        assert candidate["axis_values"] == {}
        assert candidate["oos_window_count"] == 1
        assert candidate["in_sample_window_count"] == 1
        full = client.get(f"/api/v1/research/studies/{body['study_fingerprint']}?detail=full")
        assert full.status_code == 200, full.text
        assert len(full.json()["windows"]) == len(body["windows"])
        missing = client.get("/api/v1/research/studies/" + "sha256:" + ("f" * 64))
        assert missing.status_code == 404


def test_plan_study_rejects_an_evaluation_window_that_cannot_fold() -> None:
    """A walk-forward request that cannot form one fold is a caller error."""
    client, _, _ = _client()
    with client:
        fingerprint = _publish_reference(client)
        response = client.post(
            "/api/v1/research/studies/plan",
            json={
                "kind": "walk_forward",
                "evaluation_start": "2026-01-01T00:00:00Z",
                "evaluation_end": "2026-01-02T00:00:00Z",
                "initial_quote_balance": "10000",
                "maker_fee_rate": "0.001",
                "taker_fee_rate": "0.002",
                "fixed_slippage_bps": "10",
                "strategy_id": fingerprint,
                "dataset_fingerprint": "sha256:" + "b" * 64,
                "in_sample_bars": 48,
                "out_of_sample_bars": 24,
                "step_bars": 24,
            },
        )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "study_window_rejected"


def test_plan_study_rejects_first_bar_start_with_suggested_range() -> None:
    """plan-study must 422 with the same warmup suggestion child backtests use."""
    client, _, _ = _client(
        dataset_store=_CoveringDatasetStore(
            starts_at="2026-01-01T00:00:00Z",
            ends_at="2026-01-11T00:00:00Z",
        )
    )
    with client:
        fingerprint = _publish_reference(client)
        response = client.post("/api/v1/research/studies/plan", json=_holdout_body(fingerprint))
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "study_window_rejected"
    assert "evaluation_start" in detail["message"]
    assert "Suggested range:" in detail["message"]


def test_async_study_submission_returns_job_and_polls_to_completion() -> None:
    """Long composed studies can queue with HTTP 202 and poll job status."""
    client, _, submitter = _client()
    with client:
        fingerprint = _publish_reference(client)
        accepted = client.post(
            "/api/v1/research/studies?async=true",
            json=_holdout_body(fingerprint),
        )
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]
        polled = client.get(f"/api/v1/research/jobs/{job_id}")
        assert polled.status_code == 200, polled.text
        body = polled.json()
        assert body["kind"] == "study"
        assert body["status"] in {"queued", "running", "completed"}
        if body["status"] == "completed":
            assert submitter.calls == 2
            assert body["study_fingerprint"].startswith("sha256:")


def test_submit_parameter_sweep_publishes_derived_axis_candidates() -> None:
    """Submitting a parameter-axis sweep persists missing derived fingerprints."""
    client, publications, submitter = _client()
    with client:
        fingerprint = _publish_reference(client)
        response = client.post(
            "/api/v1/research/studies",
            json={
                "kind": "parameter_sweep",
                "evaluation_start": "2026-01-01T00:00:00Z",
                "evaluation_end": "2026-01-11T00:00:00Z",
                "initial_quote_balance": "10000",
                "maker_fee_rate": "0.001",
                "taker_fee_rate": "0.002",
                "fixed_slippage_bps": "10",
                "strategy_id": fingerprint,
                "dataset_fingerprint": "sha256:" + "b" * 64,
                "parameter_axes": [
                    {
                        "indicator_id": "fast",
                        "parameter": "period",
                        "values": ["12", "26"],
                    }
                ],
            },
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "parameter_sweep"
    assert submitter.calls == 2
    assert len(publications.snapshot_fingerprints()) == 3
    assert sum(1 for window in body["windows"] if window.get("selected") is True) == 1
    assert body["selection_metric"] == "total_return_fraction"
    assert "stitched_oos_equity" not in body


def test_study_catalog_supports_limit_100_offset_pagination() -> None:
    """The list route honors the le=100 bound plus offset for readback scans."""
    client, _, _ = _client()
    with client:
        fingerprint = _publish_reference(client)
        first = client.post("/api/v1/research/studies", json=_holdout_body(fingerprint))
        assert first.status_code == 201, first.text
        ok = client.get("/api/v1/research/studies?limit=100")
        assert ok.status_code == 200, ok.text
        assert len(ok.json()["studies"]) == 1
        page_two = client.get("/api/v1/research/studies?limit=100&offset=1")
        assert page_two.status_code == 200, page_two.text
        assert page_two.json()["studies"] == []
        rejected = client.get("/api/v1/research/studies?limit=200")
        assert rejected.status_code == 422
        negative = client.get("/api/v1/research/studies?offset=-1")
        assert negative.status_code == 422


def test_find_study_by_request_reads_back_through_the_real_route() -> None:
    """The ambiguous-submit recovery command must work against the real API."""
    client, _, _ = _client()
    with client:
        fingerprint = _publish_reference(client)
        submitted = client.post("/api/v1/research/studies", json=_holdout_body(fingerprint))
        assert submitted.status_code == 201, submitted.text
        request_fp = submitted.json()["request_fingerprint"]
        assert isinstance(request_fp, str)

        def route_through_test_client(
            *, method: str, url: str, **_kwargs: object
        ) -> dict[str, object]:
            """Serve the CLI's loopback request via this TestClient."""
            response = client.request(method, url.replace("http://127.0.0.1:8000", ""))
            assert response.status_code == 200, response.text
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("Study catalog response was not a JSON object.")
            return payload

        with patch(
            "thytrader.research.http.request_json",
            side_effect=route_through_test_client,
        ):
            row = json.loads(find_study_by_request("http://127.0.0.1:8000", request_fp))
        assert row["study_fingerprint"] == submitted.json()["study_fingerprint"]
        assert row["request_fingerprint"] == request_fp


class _NoApiPlanning(ResearchStudyService):
    """Dependency used only by API submits to forbid expensive inline planning."""

    async def plan(
        self,
        request: ResearchStudyRequest,
        *,
        budget: StudyBudget = ASYNC_STUDY_BUDGET,
    ) -> ResearchStudyPlan:
        """Fail if the async handler tries to plan before persisting its job."""
        del request, budget
        raise AssertionError("Async submit must leave planning to the research worker.")


def test_async_submit_does_not_call_api_planner() -> None:
    """A 202 still echoes pinned inputs even when inline planning is forbidden."""
    client, publications, submitter = _client()
    assert isinstance(client.app, FastAPI)

    def api_service() -> ResearchStudyService:
        """Return the API-only planner guard; the worker keeps its own composition."""
        return _NoApiPlanning(
            publications=publications, submitter=submitter, results=_StudyResults()
        )

    client.app.dependency_overrides[_study_service] = api_service
    with client:
        identity = _publish_reference(client)
        response = client.post("/api/v1/research/studies?async=true", json=_holdout_body(identity))
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["strategy_fingerprint"].startswith("sha256:")
        assert body["evaluation_start"] == "2026-01-01T00:00:00Z"
        assert body["evaluation_end"] == "2026-01-11T00:00:00Z"


def test_async_infeasible_plan_is_a_durable_job_failure() -> None:
    """Planning errors move to the queued job, retaining a typed phase and error code."""
    client, _, _ = _client()
    with client:
        identity = _publish_reference(client)
        payload = _holdout_body(identity)
        payload.pop("oos_fraction")
        payload.update(
            kind="walk_forward", in_sample_bars=10000, out_of_sample_bars=1000, step_bars=1000
        )
        response = client.post("/api/v1/research/studies?async=true", json=payload)
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = client.get(f"/api/v1/research/jobs/{job_id}").json()
            if job["status"] == "failed":
                break
            time.sleep(0.01)
        assert job["status"] == "failed", job
        assert job["failed_phase"] == "plan"
        assert job["error_code"] == "study_window_rejected"
        assert job["failed_detail"]
