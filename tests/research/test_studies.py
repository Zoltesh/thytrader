"""Unit tests for walk-forward, OOS, cross-market, sweep, and WFO studies."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from thytrader.backtest.models import BacktestResult, BacktestSummary, EquityPoint
from thytrader.backtest.submission import BacktestSubmissionRequest, BacktestSubmissionResult
from thytrader.market_data.datasets import DatasetManifest
from thytrader.research.models import IndicatorTimeframeDataset
from thytrader.research.parameter_sweep import ParameterAxis, SelectionMetric
from thytrader.research.studies import (
    FoldMode,
    MarketBinding,
    ResearchStudyRequest,
    ResearchStudyService,
    StudyKind,
    StudyPlanningError,
    StudyWindowResult,
    WindowRole,
    aggregate_windows,
    plan_study,
    request_fingerprint,
    window_submission_request,
)
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.publication import PublishedStrategy

if TYPE_CHECKING:
    from thytrader.market_data.datasets import DatasetStore
    from thytrader.persistence.backtest_results import (
        BacktestResultReader,
        BacktestResultSummaryView,
    )
    from thytrader.strategies.publication import StrategyPublicationStore


def _published(product_id: str, timeframe: str = "1h") -> PublishedStrategy:
    """Build a minimal published strategy stand-in for window planning."""
    definition = SimpleNamespace(
        instrument=SimpleNamespace(product_id=product_id),
        timeframe=timeframe,
    )
    return cast("PublishedStrategy", SimpleNamespace(definition=definition))


def _result_with_summary(summary: BacktestSummary) -> BacktestResult:
    """Build a V1 result document that carries only the summary under test."""
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
        summary=summary,
    )


def _holdout() -> ResearchStudyRequest:
    """Return a valid one-market OOS holdout request."""
    return ResearchStudyRequest(
        kind=StudyKind.OOS_HOLDOUT,
        evaluation_start=datetime(2026, 1, 1, tzinfo=UTC),
        evaluation_end=datetime(2026, 1, 11, tzinfo=UTC),
        initial_quote_balance="10000",
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps="10",
        engine_contract_version="thytrader-bar-backtest-v1",
        strategy_fingerprint="sha256:" + "a" * 64,
        dataset_fingerprint="sha256:" + "b" * 64,
        oos_fraction="0.3",
    )


def test_oos_holdout_splits_last_fraction_to_out_of_sample() -> None:
    """Ten 1h days with 0.3 OOS leaves seven IS bars-days then three OOS days."""
    request = _holdout()
    plan = plan_study(
        request,
        publications={request.strategy_fingerprint or "": _published("BTC-USD")},
    )
    assert len(plan.windows) == 2
    insample, oos = plan.windows
    assert insample.role is WindowRole.IN_SAMPLE
    assert oos.role is WindowRole.OUT_OF_SAMPLE
    assert insample.evaluation_start == datetime(2026, 1, 1, tzinfo=UTC)
    assert insample.evaluation_end == datetime(2026, 1, 8, tzinfo=UTC)
    assert oos.evaluation_start == datetime(2026, 1, 8, tzinfo=UTC)
    assert oos.evaluation_end == datetime(2026, 1, 11, tzinfo=UTC)


def test_plan_study_forwards_indicator_dataset_fingerprints() -> None:
    """Child windows and submissions keep unbound extra-TF dataset bindings."""
    extra = (IndicatorTimeframeDataset(timeframe="1h", dataset_fingerprint="sha256:" + "c" * 64),)
    request = _holdout().model_copy(update={"indicator_dataset_fingerprints": extra})
    plan = plan_study(
        request,
        publications={request.strategy_fingerprint or "": _published("BTC-USD")},
    )
    assert all(window.indicator_dataset_fingerprints == extra for window in plan.windows)
    child = window_submission_request(request, plan.windows[0])
    assert child.indicator_dataset_fingerprints == extra


def test_walk_forward_rolling_emits_nonoverlapping_oos_when_step_equals_oos() -> None:
    """Two complete folds fit a 20-day 1h window with 7d IS, 3d OOS, 3d step."""
    fingerprint = "sha256:" + "a" * 64
    request = ResearchStudyRequest(
        kind=StudyKind.WALK_FORWARD,
        evaluation_start=datetime(2026, 1, 1, tzinfo=UTC),
        evaluation_end=datetime(2026, 1, 21, tzinfo=UTC),
        initial_quote_balance="10000",
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps="10",
        engine_contract_version="thytrader-bar-backtest-v1",
        strategy_fingerprint=fingerprint,
        dataset_fingerprint="sha256:" + "b" * 64,
        in_sample_bars=7 * 24,
        out_of_sample_bars=3 * 24,
        step_bars=3 * 24,
        fold_mode=FoldMode.ROLLING,
    )
    plan = plan_study(request, publications={fingerprint: _published("BTC-USD")})
    oos = [window for window in plan.windows if window.role is WindowRole.OUT_OF_SAMPLE]
    assert len(oos) >= 2
    assert oos[0].evaluation_end == oos[1].evaluation_start
    assert plan.warnings == ()


def test_walk_forward_overlapping_oos_adds_a_warning() -> None:
    """A step shorter than OOS discloses overlapping out-of-sample windows."""
    fingerprint = "sha256:" + "a" * 64
    request = ResearchStudyRequest(
        kind=StudyKind.WALK_FORWARD,
        evaluation_start=datetime(2026, 1, 1, tzinfo=UTC),
        evaluation_end=datetime(2026, 1, 21, tzinfo=UTC),
        initial_quote_balance="10000",
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps="10",
        engine_contract_version="thytrader-bar-backtest-v1",
        strategy_fingerprint=fingerprint,
        dataset_fingerprint="sha256:" + "b" * 64,
        in_sample_bars=7 * 24,
        out_of_sample_bars=3 * 24,
        step_bars=24,
        fold_mode=FoldMode.ROLLING,
    )
    plan = plan_study(request, publications={fingerprint: _published("BTC-USD")})
    assert any("overlap" in warning for warning in plan.warnings)


def test_cross_market_requires_distinct_products() -> None:
    """Two bindings on BTC-USD cannot pretend to be cross-market research."""
    btc = "sha256:" + "1" * 64
    also_btc = "sha256:" + "2" * 64
    request = ResearchStudyRequest(
        kind=StudyKind.CROSS_MARKET,
        evaluation_start=datetime(2026, 1, 1, tzinfo=UTC),
        evaluation_end=datetime(2026, 2, 1, tzinfo=UTC),
        initial_quote_balance="10000",
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps="10",
        engine_contract_version="thytrader-bar-backtest-v1",
        markets=(
            MarketBinding(strategy_fingerprint=btc, dataset_fingerprint="sha256:" + "b" * 64),
            MarketBinding(strategy_fingerprint=also_btc, dataset_fingerprint="sha256:" + "c" * 64),
        ),
    )
    with pytest.raises(StudyPlanningError, match="distinct products"):
        plan_study(
            request,
            publications={
                btc: _published("BTC-USD"),
                also_btc: _published("BTC-USD"),
            },
        )


def test_cross_market_emits_one_full_window_per_product() -> None:
    """BTC and ETH share the evaluation window and keep distinct child labels."""
    btc = "sha256:" + "1" * 64
    eth = "sha256:" + "2" * 64
    request = ResearchStudyRequest(
        kind=StudyKind.CROSS_MARKET,
        evaluation_start=datetime(2026, 1, 1, tzinfo=UTC),
        evaluation_end=datetime(2026, 2, 1, tzinfo=UTC),
        initial_quote_balance="10000",
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps="10",
        engine_contract_version="thytrader-bar-backtest-v3",
        markets=(
            MarketBinding(strategy_fingerprint=btc, dataset_fingerprint="sha256:" + "b" * 64),
            MarketBinding(strategy_fingerprint=eth, dataset_fingerprint="sha256:" + "c" * 64),
        ),
    )
    plan = plan_study(
        request,
        publications={btc: _published("BTC-USD"), eth: _published("ETH-USD")},
    )
    assert [window.product_id for window in plan.windows] == ["BTC-USD", "ETH-USD"]
    assert all(window.role is WindowRole.FULL_WINDOW for window in plan.windows)


def test_aggregate_does_not_treat_in_sample_as_oos_performance() -> None:
    """OOS mean return ignores in-sample windows."""
    summary_is = BacktestSummary(
        initial_equity="10000",
        final_equity="12000",
        total_net_pnl="2000",
        total_return_fraction="0.2",
        gross_profit="2000",
        gross_loss="0",
        win_rate="1",
        trade_count=2,
        winning_trade_count=2,
        maximum_drawdown="0",
        maximum_drawdown_fraction="0",
        exposure_bars=10,
        evaluation_bars=10,
    )
    summary_oos = summary_is.model_copy(
        update={"total_return_fraction": "0.05", "trade_count": 1, "winning_trade_count": 1}
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    aggregate = aggregate_windows(
        (
            StudyWindowResult(
                label="in_sample-0",
                role=WindowRole.IN_SAMPLE,
                fold_index=0,
                product_id="BTC-USD",
                run_fingerprint="sha256:" + "1" * 64,
                result_fingerprint="sha256:" + "2" * 64,
                strategy_fingerprint="sha256:" + "a" * 64,
                evaluation_start=start,
                evaluation_end=end,
                summary=summary_is,
            ),
            StudyWindowResult(
                label="out_of_sample-0",
                role=WindowRole.OUT_OF_SAMPLE,
                fold_index=0,
                product_id="BTC-USD",
                run_fingerprint="sha256:" + "3" * 64,
                result_fingerprint="sha256:" + "4" * 64,
                strategy_fingerprint="sha256:" + "a" * 64,
                evaluation_start=start,
                evaluation_end=end,
                summary=summary_oos,
            ),
        )
    )
    assert aggregate.mean_oos_return_fraction == "0.05"
    assert aggregate.mean_is_return_fraction == "0.2"
    assert aggregate.oos_trade_count == 1
    assert aggregate.is_oos_return_gap == "0.15"


def test_aggregate_is_only_leaves_oos_fields_empty() -> None:
    """In-sample-only studies must not populate OOS aggregate fields from IS windows."""
    summary_is = BacktestSummary(
        initial_equity="10000",
        final_equity="12000",
        total_net_pnl="2000",
        total_return_fraction="0.2",
        gross_profit="2000",
        gross_loss="0",
        win_rate="1",
        trade_count=2,
        winning_trade_count=2,
        maximum_drawdown="0",
        maximum_drawdown_fraction="0",
        exposure_bars=10,
        evaluation_bars=10,
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    aggregate = aggregate_windows(
        (
            StudyWindowResult(
                label="in_sample-0",
                role=WindowRole.IN_SAMPLE,
                fold_index=0,
                product_id="BTC-USD",
                run_fingerprint="sha256:" + "1" * 64,
                result_fingerprint="sha256:" + "2" * 64,
                strategy_fingerprint="sha256:" + "a" * 64,
                evaluation_start=start,
                evaluation_end=end,
                summary=summary_is,
            ),
        )
    )
    assert aggregate.oos_window_count == 0
    assert aggregate.oos_trade_count == 0
    assert aggregate.mean_oos_return_fraction is None
    assert aggregate.mean_oos_drawdown_fraction is None
    assert aggregate.oos_win_rate is None
    assert aggregate.mean_is_return_fraction == "0.2"
    assert aggregate.is_oos_return_gap is None


def test_oos_holdout_embargo_leaves_unused_bars_between_windows() -> None:
    """Embargo bars are skipped and are not part of either child window."""
    request = _holdout().model_copy(update={"embargo_bars": 24})
    fingerprint = request.strategy_fingerprint or ""
    plan = plan_study(request, publications={fingerprint: _published("BTC-USD")})
    insample, oos = plan.windows
    assert insample.evaluation_end == datetime(2026, 1, 7, tzinfo=UTC)
    assert oos.evaluation_start == datetime(2026, 1, 8, tzinfo=UTC)


def test_walk_forward_rejects_a_window_too_short_for_one_fold() -> None:
    """Planning fails closed instead of emitting a partial fold."""
    fingerprint = "sha256:" + "a" * 64
    request = ResearchStudyRequest(
        kind=StudyKind.WALK_FORWARD,
        evaluation_start=datetime(2026, 1, 1, tzinfo=UTC),
        evaluation_end=datetime(2026, 1, 2, tzinfo=UTC),
        initial_quote_balance="10000",
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps="10",
        engine_contract_version="thytrader-bar-backtest-v1",
        strategy_fingerprint=fingerprint,
        dataset_fingerprint="sha256:" + "b" * 64,
        in_sample_bars=48,
        out_of_sample_bars=24,
        step_bars=24,
    )
    with pytest.raises(StudyPlanningError, match="too short"):
        plan_study(request, publications={fingerprint: _published("BTC-USD")})


def test_request_fingerprint_is_stable_for_identical_assumptions() -> None:
    """Repeating the same study request yields the same content identity."""
    first = request_fingerprint(_holdout())
    second = request_fingerprint(_holdout())
    assert first == second
    assert first.startswith("sha256:")
    payload = _holdout().model_dump(mode="json", exclude_none=True)
    assert "candidate_strategy_fingerprints" not in payload
    assert "parameter_axes" not in payload
    assert "selection_metric" not in payload


def test_submit_reuses_child_identities_and_does_not_count_is_as_oos() -> None:
    """Service submit records every child and aggregates OOS windows only."""
    request = _holdout()
    summary = BacktestSummary(
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

    class _Publications:
        """Return the planning stand-in for the requested fingerprint."""

        async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
            """Load the BTC hourly stand-in."""
            del strategy_fingerprint_value
            return _published("BTC-USD")

        async def publish(self, definition: StrategyDefinition) -> PublishedStrategy:
            """Unused in this test."""
            del definition
            raise RuntimeError("unused")

        async def publish_draft(
            self, definition: StrategyDefinition, *, expected_revision: int
        ) -> PublishedStrategy:
            """Unused in this test."""
            del definition, expected_revision
            raise RuntimeError("unused")

    class _Submitter:
        """Return a distinct identity pair per child window."""

        def __init__(self) -> None:
            """Start with no submissions."""
            self.calls = 0

        async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
            """Count submissions and return dummy fingerprints."""
            del request
            self.calls += 1
            suffix = str(self.calls)
            return BacktestSubmissionResult(
                run_fingerprint="sha256:" + suffix.ljust(64, "c"),
                result_fingerprint="sha256:" + suffix.ljust(64, "d"),
            )

    class _Results:
        """Return the same summary for every child result."""

        async def list_summaries(
            self,
            *,
            run_fingerprint: str | None = None,
            strategy_fingerprint: str | None = None,
            dataset_fingerprint: str | None = None,
            limit: int,
            offset: int,
        ) -> tuple[BacktestResultSummaryView, ...]:
            """Unused by submit."""
            del run_fingerprint, strategy_fingerprint, dataset_fingerprint, limit, offset
            return ()

        async def load(self, result_fingerprint: str) -> BacktestResult:
            """Return a V1 child result with the fixed summary."""
            del result_fingerprint
            return _result_with_summary(summary)

    submitter = _Submitter()
    service = ResearchStudyService(
        publications=_Publications(),
        submitter=submitter,
        results=_Results(),
    )
    study = asyncio.run(service.submit(request))
    assert submitter.calls == 2
    assert study.kind is StudyKind.OOS_HOLDOUT
    assert len(study.windows) == 2
    assert study.aggregate.oos_window_count == 1
    assert study.aggregate.oos_trade_count == 1
    assert study.study_fingerprint.startswith("sha256:")
    assert study.study_fingerprint != "sha256:" + ("0" * 64)


_REFERENCE = Path(__file__).parents[1] / "strategies" / "golden" / "reference_strategy_v1.json"


def _reference_publication() -> PublishedStrategy:
    """Load the golden reference as a published strategy."""
    definition = StrategyDefinition.model_validate_json(_REFERENCE.read_text(encoding="utf-8"))
    fingerprint = strategy_fingerprint(definition)
    return PublishedStrategy(strategy_fingerprint=fingerprint, definition=definition)


def test_parameter_sweep_emits_one_window_per_axis_cell() -> None:
    """A two-value EMA period axis plans two sweep children on the same bounds."""
    published = _reference_publication()
    request = ResearchStudyRequest(
        kind=StudyKind.PARAMETER_SWEEP,
        evaluation_start=datetime(2026, 1, 1, tzinfo=UTC),
        evaluation_end=datetime(2026, 1, 11, tzinfo=UTC),
        initial_quote_balance="10000",
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps="10",
        engine_contract_version="thytrader-bar-backtest-v1",
        strategy_fingerprint=published.strategy_fingerprint,
        dataset_fingerprint="sha256:" + "b" * 64,
        parameter_axes=(
            ParameterAxis(indicator_id="ema_fast", parameter="period", values=("12", "26")),
        ),
    )
    plan = plan_study(request, publications={published.strategy_fingerprint: published})
    assert len(plan.windows) == 2
    assert all(window.role is WindowRole.SWEEP_CANDIDATE for window in plan.windows)
    assert plan.windows[0].strategy_fingerprint != plan.windows[1].strategy_fingerprint
    assert plan.windows[0].evaluation_start == request.evaluation_start
    assert any("not an out-of-sample claim" in warning for warning in plan.warnings)


def test_walk_forward_optimization_selects_on_in_sample_only() -> None:
    """A worse OOS must not win when its in-sample score is lower."""
    published = _reference_publication()
    weak = "sha256:" + "1" * 64
    strong_is = "sha256:" + "2" * 64
    request = ResearchStudyRequest(
        kind=StudyKind.WALK_FORWARD_OPTIMIZATION,
        evaluation_start=datetime(2026, 1, 1, tzinfo=UTC),
        evaluation_end=datetime(2026, 1, 21, tzinfo=UTC),
        initial_quote_balance="10000",
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps="10",
        engine_contract_version="thytrader-bar-backtest-v1",
        strategy_fingerprint=published.strategy_fingerprint,
        dataset_fingerprint="sha256:" + "b" * 64,
        in_sample_bars=7 * 24,
        out_of_sample_bars=3 * 24,
        step_bars=3 * 24,
        fold_mode=FoldMode.ROLLING,
        candidate_strategy_fingerprints=(weak, strong_is),
        selection_metric=SelectionMetric.TOTAL_RETURN_FRACTION,
    )
    stand_in = _published("BTC-USD")
    publications = {
        published.strategy_fingerprint: published,
        weak: stand_in,
        strong_is: stand_in,
    }
    planned = plan_study(request, publications=publications)
    returns = {
        weak: {WindowRole.IN_SAMPLE: "0.01", WindowRole.OUT_OF_SAMPLE: "0.9"},
        strong_is: {WindowRole.IN_SAMPLE: "0.2", WindowRole.OUT_OF_SAMPLE: "0.02"},
    }
    ordered_returns = [
        returns[window.strategy_fingerprint][window.role] for window in planned.windows
    ]

    class _Publications:
        """Serve the base plus two candidate stand-ins on BTC-USD 1h."""

        async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
            """Return the golden document for identity and BTC stand-ins for candidates."""
            loaded = publications.get(strategy_fingerprint_value)
            if loaded is None:
                raise RuntimeError("unexpected fingerprint")
            return loaded

        async def publish(self, definition: StrategyDefinition) -> PublishedStrategy:
            """Unused in the candidate-fingerprint path."""
            del definition
            raise RuntimeError("unused")

        async def publish_draft(
            self, definition: StrategyDefinition, *, expected_revision: int
        ) -> PublishedStrategy:
            """Unused in this test."""
            del definition, expected_revision
            raise RuntimeError("unused")

    class _Submitter:
        """Return identities that encode the submitted strategy fingerprint."""

        def __init__(self) -> None:
            """Start empty."""
            self.calls = 0

        async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
            """Mint a fingerprint pair from the call index."""
            del request
            self.calls += 1
            suffix = str(self.calls)
            return BacktestSubmissionResult(
                run_fingerprint="sha256:" + suffix.ljust(64, "c"),
                result_fingerprint="sha256:" + suffix.ljust(64, "d"),
            )

    class _Results:
        """Return returns that would fool a lookahead selector."""

        async def list_summaries(
            self,
            *,
            run_fingerprint: str | None = None,
            strategy_fingerprint: str | None = None,
            dataset_fingerprint: str | None = None,
            limit: int,
            offset: int,
        ) -> tuple[BacktestResultSummaryView, ...]:
            """Unused by submit."""
            del run_fingerprint, strategy_fingerprint, dataset_fingerprint, limit, offset
            return ()

        async def load(self, result_fingerprint: str) -> BacktestResult:
            """Map result identity back to the planned child order."""
            index = int(result_fingerprint.removeprefix("sha256:").rstrip("d")) - 1
            return _result_with_summary(
                BacktestSummary(
                    initial_equity="10000",
                    final_equity="10100",
                    total_net_pnl="100",
                    total_return_fraction=ordered_returns[index],
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
            )

    service = ResearchStudyService(
        publications=_Publications(),
        submitter=_Submitter(),
        results=_Results(),
    )
    study = asyncio.run(service.submit(request))
    selected_oos = [
        window
        for window in study.windows
        if window.role is WindowRole.OUT_OF_SAMPLE and window.selected
    ]
    assert selected_oos
    assert all(window.strategy_fingerprint == strong_is for window in selected_oos)
    assert study.aggregate.mean_oos_return_fraction == "0.02"
    assert study.stitched_oos_equity is not None


def test_plan_service_rejects_warmup_infeasible_evaluation_start() -> None:
    """plan-study must share backtest bound checks so first-bar starts 422."""
    published = _reference_publication()
    request = ResearchStudyRequest(
        kind=StudyKind.OOS_HOLDOUT,
        evaluation_start=datetime(2026, 1, 1, tzinfo=UTC),
        evaluation_end=datetime(2026, 1, 11, tzinfo=UTC),
        initial_quote_balance="10000",
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps="10",
        engine_contract_version="thytrader-bar-backtest-v1",
        strategy_fingerprint=published.strategy_fingerprint,
        dataset_fingerprint="sha256:" + "b" * 64,
        oos_fraction="0.3",
    )

    class _Publications:
        async def load(self, fingerprint: str) -> PublishedStrategy:
            del fingerprint
            return published

    class _Datasets:
        def load_manifest(self, fingerprint: str) -> DatasetManifest:
            del fingerprint
            return DatasetManifest(
                provider="coinbase",
                product_id="BTC-USD",
                timeframe="1h",
                starts_at="2026-01-01T00:00:00Z",
                ends_at="2026-01-11T00:00:00Z",
                expected_candle_count=241,
                received_candle_count=241,
                gap_count=0,
                missing_intervals=0,
                complete=True,
                content_fingerprint="sha256:" + "b" * 64,
                files=(Path("unused.parquet"),),
                manifest_path=Path("unused.json"),
            )

    class _Submitter:
        async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
            del request
            raise AssertionError("planning must not submit child backtests")

    class _Results:
        async def list_summaries(self, **kwargs: object) -> tuple[object, ...]:
            del kwargs
            return ()

        async def load(self, result_fingerprint: str) -> BacktestResult:
            del result_fingerprint
            raise AssertionError("planning must not load results")

    service = ResearchStudyService(
        publications=cast("StrategyPublicationStore", _Publications()),
        submitter=_Submitter(),
        results=cast("BacktestResultReader", _Results()),
        datasets=cast("DatasetStore", _Datasets()),
    )
    with pytest.raises(StudyPlanningError, match="evaluation_start") as raised:
        asyncio.run(service.plan(request))
    assert "Suggested range:" in str(raised.value)
