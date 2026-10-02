"""Study summaries explain every row: axis values, bounds, per-candidate OOS, thinned stitch."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from thytrader.backtest.models import BacktestSummary
from thytrader.research.parameter_sweep import (
    SUMMARY_STITCHED_POINTS,
    ParameterAxis,
    StitchedEquityPoint,
    StitchedOosEquity,
    SweepAxisTarget,
    candidate_axis_values,
    derive_parameter_candidates,
    downsample_stitched_points,
)
from thytrader.research.studies import (
    ResearchStudy,
    StudyAggregate,
    StudyKind,
    StudyWindowResult,
    WindowRole,
    load_candidate_definitions,
    summarize_research_study,
)
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint

if TYPE_CHECKING:
    from thytrader.strategies.snapshots import StrategySnapshot

_REFERENCE = Path(__file__).parents[1] / "strategies" / "golden" / "reference_strategy_v1.json"
_START = datetime(2026, 1, 1, tzinfo=UTC)


def _base() -> StrategyDefinition:
    """Load the golden EMA-cross reference (ema_fast 20, ema_slow 50)."""
    return StrategyDefinition.model_validate_json(_REFERENCE.read_text(encoding="utf-8"))


def _grid() -> tuple[StrategySnapshot, ...]:
    """Derive the 2 x 2 fast/slow period grid the way a WFO request does."""
    base = _base()
    axes = (
        ParameterAxis(indicator_id="ema_fast", parameter="period", values=("10", "20")),
        ParameterAxis(indicator_id="ema_slow", parameter="period", values=("100", "200")),
    )
    return derive_parameter_candidates(base, axes, base_fingerprint=strategy_fingerprint(base))


def _summary(pnl: str) -> BacktestSummary:
    """One child summary with the given net PnL."""
    return BacktestSummary(
        initial_equity="10000",
        final_equity=str(10000 + int(pnl)),
        total_net_pnl=pnl,
        total_return_fraction=str(int(pnl) / 10000),
        gross_profit="0",
        gross_loss="0",
        win_rate="0",
        trade_count=2,
        winning_trade_count=0,
        maximum_drawdown="0",
        maximum_drawdown_fraction="0",
        exposure_bars=1,
        evaluation_bars=24,
    )


def _wfo(candidates: tuple[StrategySnapshot, ...]) -> ResearchStudy:
    """Two folds; every candidate scored in and out of sample; candidate 1 selected."""
    windows: list[StudyWindowResult] = []
    for fold in range(2):
        for role, offset in ((WindowRole.IN_SAMPLE, 0), (WindowRole.OUT_OF_SAMPLE, 7)):
            for index, candidate in enumerate(candidates):
                start = _START + timedelta(days=fold * 3 + offset)
                pnl = "-50" if role is WindowRole.OUT_OF_SAMPLE and index == 0 else "100"
                windows.append(
                    StudyWindowResult(
                        label=f"{role.value}-{fold}-{index}",
                        role=role,
                        fold_index=fold,
                        product_id="BTC-USD",
                        run_fingerprint="sha256:" + f"{fold}{index}".ljust(64, "a"),
                        result_fingerprint="sha256:" + f"{fold}{index}{offset}".ljust(64, "b"),
                        strategy_fingerprint=candidate.strategy_fingerprint,
                        evaluation_start=start,
                        evaluation_end=start + timedelta(days=3),
                        summary=_summary(pnl),
                        selected=index == 1,
                    )
                )
    return ResearchStudy(
        study_fingerprint="sha256:" + "f" * 64,
        request_fingerprint="sha256:" + "e" * 64,
        kind=StudyKind.WALK_FORWARD_OPTIMIZATION,
        windows=tuple(windows),
        aggregate=StudyAggregate(
            window_count=len(windows),
            oos_window_count=2,
            oos_trade_count=4,
            oos_winning_trade_count=0,
        ),
    )


def test_every_window_row_names_its_candidate_axis_values_and_bounds() -> None:
    """One show-study call explains each row without show-snapshot or plan-study."""
    candidates = _grid()
    study = _wfo(candidates)
    definitions = {item.strategy_fingerprint: item.definition for item in candidates}
    summary = summarize_research_study(study, definitions=definitions)
    first = summary.window_pnl[0]
    assert first.axis_values == {"ema_fast.period": 10, "ema_slow.period": 100}
    assert first.evaluation_start == _START
    assert first.evaluation_end == _START + timedelta(days=3)
    assert first.product_id == "BTC-USD"
    payload = summary.model_dump(mode="json")
    assert payload["window_pnl"][1]["axis_values"] == {
        "ema_fast.period": 10,
        "ema_slow.period": 200,
    }
    assert payload["window_pnl"][0]["evaluation_start"] == "2026-01-01T00:00:00Z"


def test_per_candidate_aggregates_show_oos_robustness_across_the_grid() -> None:
    """Every candidate's OOS windows are summed, not just the selected path."""
    candidates = _grid()
    study = _wfo(candidates)
    definitions = {item.strategy_fingerprint: item.definition for item in candidates}
    aggregates = summarize_research_study(study, definitions=definitions).candidates
    assert [item.strategy_fingerprint for item in aggregates] == [
        item.strategy_fingerprint for item in candidates
    ]
    loser, winner = aggregates[0], aggregates[1]
    assert loser.oos_window_count == 2
    assert loser.oos_total_net_pnl == "-100"
    assert loser.oos_positive_window_count == 0
    assert winner.oos_total_net_pnl == "200"
    assert winner.oos_positive_window_count == 2
    assert winner.selected_window_count == 4
    assert winner.in_sample_total_net_pnl == "200"
    assert winner.full_window_count == 0
    assert winner.full_window_total_net_pnl is None
    assert winner.axis_values == {"ema_fast.period": 10, "ema_slow.period": 200}


def test_summaries_without_definitions_keep_rows_and_leave_axis_values_empty() -> None:
    """A summary built without snapshots still states bounds and per-candidate sums."""
    summary = summarize_research_study(_wfo(_grid()))
    assert all(row.axis_values == {} for row in summary.window_pnl)
    assert len(summary.candidates) == 4


def test_axis_values_cover_sizing_exits_and_literals() -> None:
    """Non-indicator axes use the derived-name labels; equal coordinates are omitted."""
    base = _base()
    axes = (
        ParameterAxis(
            target=SweepAxisTarget.SIZING, parameter="risk_fraction", values=("0.005", "0.01")
        ),
        ParameterAxis(
            target=SweepAxisTarget.ENTRY_LITERAL,
            indicator_id="rsi",
            parameter="literal",
            values=("50", "55"),
        ),
        ParameterAxis(
            target=SweepAxisTarget.EXITS, parameter="initial_stop_multiple", values=("2", "3")
        ),
    )
    derived = derive_parameter_candidates(base, axes, base_fingerprint=strategy_fingerprint(base))
    values = candidate_axis_values({item.strategy_fingerprint: item.definition for item in derived})
    last = values[derived[-1].strategy_fingerprint]
    assert last == {
        "sizing.risk_fraction": "0.01",
        "exits.initial_stop_multiple": "3",
        "entry_literal.rsi.literal": "55",
    }
    assert candidate_axis_values({"sha256:" + "a" * 64: base}) == {"sha256:" + "a" * 64: {}}


def _stitched(count: int) -> StitchedOosEquity:
    """A rising stitched path with one deep dip in the middle."""
    points = tuple(
        StitchedEquityPoint(
            candle_starts_at=_START + timedelta(hours=index),
            equity="5000" if index == count // 2 else str(10000 + index),
            fold_index=0,
            result_fingerprint="sha256:" + "c" * 64,
        )
        for index in range(count)
    )
    return StitchedOosEquity(available=True, point_count=count, points=points)


def test_stitched_points_are_thinned_with_the_full_count_and_the_drawdown_kept() -> None:
    """``points`` is no longer empty; it is thinned like the portfolio chart."""
    study = _wfo(_grid()).model_copy(update={"stitched_oos_equity": _stitched(1081)})
    summary = summarize_research_study(study)
    stitched = summary.stitched_oos_equity
    assert stitched is not None
    assert stitched.point_count == 1081
    assert 0 < len(stitched.points) <= SUMMARY_STITCHED_POINTS
    assert summary.stitched_oos_points_downsampled is True
    assert stitched.points[0].candle_starts_at == _START
    assert stitched.points[-1].candle_starts_at == _START + timedelta(hours=1080)
    assert min(Decimal(point.equity) for point in stitched.points) == Decimal(5000)


def test_short_stitched_paths_are_returned_whole() -> None:
    """Paths at or under the display cap keep every mark."""
    stitched = _stitched(50)
    assert downsample_stitched_points(stitched.points) == stitched.points
    study = _wfo(_grid()).model_copy(update={"stitched_oos_equity": stitched})
    summary = summarize_research_study(study)
    assert summary.stitched_oos_points_downsampled is False
    assert summary.stitched_oos_equity is not None
    assert len(summary.stitched_oos_equity.points) == 50


def test_unreadable_candidate_snapshots_are_skipped() -> None:
    """A snapshot that cannot be loaded leaves that candidate without axis values."""
    candidates = _grid()
    known = {item.strategy_fingerprint: item for item in candidates[1:]}

    class _Publications:
        """Serve every candidate except the first."""

        async def load(self, strategy_fingerprint_value: str) -> StrategySnapshot:
            """Return a known snapshot or fail like a deleted one."""
            if strategy_fingerprint_value not in known:
                raise LookupError("snapshot was deleted")
            return known[strategy_fingerprint_value]

    definitions = asyncio.run(load_candidate_definitions(_Publications(), _wfo(candidates)))
    assert set(definitions) == set(known)
