"""HTTP tests for Phase 11 templates, engine-support, and research studies."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from tests.api.test_strategy_authoring import (
    InMemoryStrategyDraftStore,
    InMemoryStrategyPublicationStore,
)
from thytrader.api.app import create_app
from thytrader.backtest.models import BacktestResult, BacktestSummary, EquityPoint
from thytrader.backtest.submission import (
    BacktestSubmissionRequest,
    BacktestSubmissionResult,
)
from thytrader.config import Settings
from thytrader.research.catalog import InMemoryResearchStudyCatalog
from thytrader.strategies.models import strategy_fingerprint

if TYPE_CHECKING:
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
    """Build a V1 result document for study child loads."""
    fingerprint = "sha256:" + "e" * 64
    return BacktestResult(
        schema_version="1.0",
        engine_contract_version="thytrader-bar-backtest-v1",
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
        limit: int,
        offset: int,
    ) -> tuple[BacktestResultSummaryView, ...]:
        """Unused by study routes."""
        del run_fingerprint, strategy_fingerprint, dataset_fingerprint, limit, offset
        return ()

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Return a V1 child result with a constant summary."""
        del result_fingerprint
        return _result_with_summary()


def _client() -> tuple[TestClient, InMemoryStrategyPublicationStore, _StudySubmitter]:
    """Build an API client with in-memory research stores."""
    drafts = InMemoryStrategyDraftStore()
    publications = InMemoryStrategyPublicationStore(drafts)
    submitter = _StudySubmitter()
    app = create_app(
        Settings(_env_file=None),
        strategy_draft_store=drafts,
        strategy_store=publications,
        backtest_submitter=submitter,
        backtest_result_store=_StudyResults(),
        research_study_catalog=InMemoryResearchStudyCatalog(),
    )
    return TestClient(app), publications, submitter


def _publish_reference(client: TestClient) -> str:
    """Create and publish the default EMA draft, returning its fingerprint."""
    created = client.post("/api/v1/strategies")
    assert created.status_code == 201, created.text
    draft = created.json()["strategy"]
    published = client.post(
        f"/api/v1/strategies/{draft['strategy_id']}/publish",
        json={"strategy": draft, "revision": 1},
    )
    assert published.status_code == 201, published.text
    fingerprint = published.json()["strategy_fingerprint"]
    assert isinstance(fingerprint, str)
    return fingerprint


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
        "engine_contract_version": "thytrader-bar-backtest-v1",
        "strategy_fingerprint": strategy_fingerprint_value,
        "dataset_fingerprint": "sha256:" + "b" * 64,
        "oos_fraction": "0.3",
    }


def test_engine_support_matrix_includes_v3_and_studies() -> None:
    """The matrix is a first-class read contract with a V3 column."""
    client, _, _ = _client()
    with client:
        response = client.get("/api/v1/research/engine-support")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["engines"] == [
        "thytrader-bar-backtest-v1",
        "thytrader-bar-backtest-v2",
        "thytrader-bar-backtest-v3",
        "thytrader-bar-backtest-v4",
    ]
    labels = {row["label"] for row in body["rows"]}
    assert "Walk-forward / OOS / cross-market studies" in labels
    assert "Maker-only / marketable entry preference" in labels


def test_templates_catalog_lists_fail_closed_ids() -> None:
    """Agents can discover the four research draft templates."""
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
    }


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
        response = client.post("/api/v1/research/studies/plan", json=_holdout_body(fingerprint))
    assert response.status_code == 200, response.text
    windows = response.json()["windows"]
    assert [window["role"] for window in windows] == ["in_sample", "out_of_sample"]
    assert submitter.calls == 0
    assert publications.published is not None
    assert strategy_fingerprint(publications.published) == fingerprint


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
        assert shown.json()["study_fingerprint"] == body["study_fingerprint"]
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
                "engine_contract_version": "thytrader-bar-backtest-v3",
                "strategy_fingerprint": fingerprint,
                "dataset_fingerprint": "sha256:" + "b" * 64,
                "in_sample_bars": 48,
                "out_of_sample_bars": 24,
                "step_bars": 24,
            },
        )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "study_window_rejected"


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
                "engine_contract_version": "thytrader-bar-backtest-v1",
                "strategy_fingerprint": fingerprint,
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
    assert len(publications.by_fingerprint) == 3
    assert sum(1 for window in body["windows"] if window.get("selected") is True) == 1
    assert body["selection_metric"] == "total_return_fraction"
    assert "stitched_oos_equity" not in body
