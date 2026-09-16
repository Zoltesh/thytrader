"""Unit tests for parameter-axis derivation, selection, and stitched OOS equity."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from thytrader.backtest.models import BacktestResult, BacktestSummary, EquityPoint
from thytrader.research.parameter_sweep import (
    ParameterAxis,
    SelectionMetric,
    StitchSourceWindow,
    SweepAxisTarget,
    apply_parameter_cell,
    derive_parameter_candidates,
    expand_parameter_grid,
    select_candidate_fingerprint,
    stitch_oos_equity,
)
from thytrader.strategies.models import (
    AllCondition,
    ComparisonCondition,
    IndicatorParameters,
    LiteralOperand,
    StrategyDefinition,
    strategy_fingerprint,
)

_REFERENCE = Path(__file__).parents[1] / "strategies" / "golden" / "reference_strategy_v1.json"


def _indicator_period(definition: StrategyDefinition, index: int) -> int:
    """Read a rolling-period parameter from one canonical indicator."""
    parameters = definition.indicators[index].parameters
    assert isinstance(parameters, IndicatorParameters)
    return parameters.period


def _reference() -> StrategyDefinition:
    """Load the golden EMA-trend publication."""
    return StrategyDefinition.model_validate_json(_REFERENCE.read_text(encoding="utf-8"))


def _summary(*, ret: str, drawdown: str = "0", pnl: str = "0") -> BacktestSummary:
    """Build a summary whose selection fields are under test."""
    return BacktestSummary(
        initial_equity="10000",
        final_equity="10100",
        total_net_pnl=pnl,
        total_return_fraction=ret,
        gross_profit="100",
        gross_loss="0",
        win_rate="1",
        trade_count=1,
        winning_trade_count=1,
        maximum_drawdown=drawdown,
        maximum_drawdown_fraction=drawdown,
        exposure_bars=10,
        evaluation_bars=10,
    )


def _result(equity: tuple[tuple[datetime, str], ...], summary: BacktestSummary) -> BacktestResult:
    """Build a V1 result whose equity curve is the stitch input."""
    fingerprint = "sha256:" + "e" * 64
    points = tuple(
        EquityPoint(
            candle_starts_at=stamp,
            cash=value,
            base_quantity="0",
            mark_price="1",
            equity=value,
        )
        for stamp, value in equity
    )
    return BacktestResult(
        schema_version="1.0",
        engine_contract_version="thytrader-bar-backtest-v1",
        run_fingerprint=fingerprint,
        strategy_fingerprint=fingerprint,
        dataset_fingerprint=fingerprint,
        signal_trace_fingerprint=fingerprint,
        trades=(),
        equity_curve=points,
        summary=summary,
    )


def test_expand_parameter_grid_is_cartesian_in_axis_order() -> None:
    """Two axes emit every pair in declaration order."""
    axes = (
        ParameterAxis(indicator_id="ema_fast", parameter="period", values=("12", "20")),
        ParameterAxis(indicator_id="ema_slow", parameter="period", values=("50", "80")),
    )
    grid = expand_parameter_grid(axes)
    assert [(cell.locator, cell.parameter, cell.value) for cell in grid[0]] == [
        ("ema_fast", "period", "12"),
        ("ema_slow", "period", "50"),
    ]
    assert [cell.identity_tuple() for cell in grid[0]] == [
        ("ema_fast", "period", "12"),
        ("ema_slow", "period", "50"),
    ]
    assert len(grid) == 4


def test_derived_candidate_fingerprint_is_stable() -> None:
    """Repeating the same axis cell yields the same published-shaped fingerprint."""
    base = _reference()
    axes = (ParameterAxis(indicator_id="ema_fast", parameter="period", values=("12", "26")),)
    first = derive_parameter_candidates(base, axes, base_fingerprint=strategy_fingerprint(base))
    second = derive_parameter_candidates(base, axes, base_fingerprint=strategy_fingerprint(base))
    assert [item.strategy_fingerprint for item in first] == [
        item.strategy_fingerprint for item in second
    ]
    periods = [_indicator_period(item.definition, 0) for item in first]
    assert periods == [12, 26]
    assert first[0].definition.strategy_id.version == 7


def test_unknown_indicator_axis_fails_closed() -> None:
    """Axes cannot invent indicator ids."""
    base = _reference()
    cell = (("missing", "period", "12"),)
    with pytest.raises(ValueError, match="not on the strategy"):
        apply_parameter_cell(base, cell, base_fingerprint=strategy_fingerprint(base))


def test_larger_period_raises_warmup_bars() -> None:
    """Substituting a longer EMA still produces a valid published document."""
    base = _reference()
    derived = apply_parameter_cell(
        base,
        (("ema_slow", "period", "80"),),
        base_fingerprint=strategy_fingerprint(base),
    )
    assert derived.data_requirements.warmup_bars >= 80
    assert _indicator_period(derived, 1) == 80


def test_selection_uses_in_sample_metric_and_fingerprint_tiebreak() -> None:
    """Higher return wins; equal returns pick the lexicographically smaller fingerprint."""
    low = "sha256:" + "a" * 64
    high = "sha256:" + "b" * 64
    assert (
        select_candidate_fingerprint(
            ((low, "0.1"), (high, "0.2")),
            SelectionMetric.TOTAL_RETURN_FRACTION,
        )
        == high
    )
    assert (
        select_candidate_fingerprint(
            ((high, "0.2"), (low, "0.2")),
            SelectionMetric.TOTAL_RETURN_FRACTION,
        )
        == low
    )
    assert (
        select_candidate_fingerprint(
            ((low, "0.3"), (high, "0.1")),
            SelectionMetric.MAXIMUM_DRAWDOWN_FRACTION,
        )
        == high
    )


def test_stitch_compounds_nonoverlapping_oos_returns() -> None:
    """Two contiguous windows compound 10% then 5% without interpolating prices."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    mid = datetime(2026, 1, 2, tzinfo=UTC)
    end = datetime(2026, 1, 3, tzinfo=UTC)
    first = _result(
        ((start, "10000"), (mid, "11000")),
        _summary(ret="0.1", pnl="1000"),
    )
    first = first.model_copy(
        update={"summary": first.summary.model_copy(update={"final_equity": "11000"})}
    )
    second = _result(
        ((mid, "10000"), (end, "10500")),
        _summary(ret="0.05", pnl="500"),
    )
    second = second.model_copy(
        update={"summary": second.summary.model_copy(update={"final_equity": "10500"})}
    )
    stitched = stitch_oos_equity(
        (
            StitchSourceWindow(
                fold_index=0,
                evaluation_start=start,
                evaluation_end=mid,
                result_fingerprint="sha256:" + "1" * 64,
                result=first,
            ),
            StitchSourceWindow(
                fold_index=1,
                evaluation_start=mid,
                evaluation_end=end,
                result_fingerprint="sha256:" + "2" * 64,
                result=second,
            ),
        )
    )
    assert stitched.available is True
    assert stitched.final_equity == "11550"
    assert stitched.total_return_fraction == "0.155"


def test_overlapping_oos_windows_refuse_stitching() -> None:
    """Overlapping OOS paths are disclosed rather than interpolated."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    mid = datetime(2026, 1, 2, tzinfo=UTC)
    end = datetime(2026, 1, 3, tzinfo=UTC)
    result = _result(((start, "10000"), (end, "11000")), _summary(ret="0.1"))
    stitched = stitch_oos_equity(
        (
            StitchSourceWindow(
                fold_index=0,
                evaluation_start=start,
                evaluation_end=end,
                result_fingerprint="sha256:" + "1" * 64,
                result=result,
            ),
            StitchSourceWindow(
                fold_index=1,
                evaluation_start=mid,
                evaluation_end=end,
                result_fingerprint="sha256:" + "2" * 64,
                result=result,
            ),
        )
    )
    assert stitched.available is False
    assert stitched.reason is not None
    assert "overlap" in stitched.reason.lower()


def test_indicator_axis_omits_default_target_from_canonical_json() -> None:
    """Default indicator axes keep ADR 0044 request fingerprints stable."""
    axis = ParameterAxis(indicator_id="ema_fast", parameter="period", values=("12", "26"))
    payload = axis.model_dump(mode="json")
    assert "target" not in payload
    assert "condition_operator" not in payload
    assert payload["indicator_id"] == "ema_fast"


def test_sizing_axis_substitutes_risk_fraction() -> None:
    """Richer sizing axes rewrite risk_fraction without changing the decision clock."""
    base = _reference()
    derived = apply_parameter_cell(
        base,
        expand_parameter_grid(
            (
                ParameterAxis(
                    target=SweepAxisTarget.SIZING,
                    parameter="risk_fraction",
                    values=("0.01", "0.02"),
                ),
            )
        )[0],
        base_fingerprint=strategy_fingerprint(base),
    )
    assert derived.sizing.risk_fraction == "0.01"
    assert derived.instrument.product_id == base.instrument.product_id
    assert derived.timeframe == base.timeframe


def test_entry_literal_axis_substitutes_the_rsi_threshold() -> None:
    """Literal axes substitute comparison thresholds without rewriting operators."""
    base = _reference()
    derived = apply_parameter_cell(
        base,
        expand_parameter_grid(
            (
                ParameterAxis(
                    target=SweepAxisTarget.ENTRY_LITERAL,
                    indicator_id="rsi",
                    parameter="literal",
                    values=("30", "40"),
                ),
            )
        )[0],
        base_fingerprint=strategy_fingerprint(base),
    )
    when = derived.entry.when
    assert isinstance(when, AllCondition)
    comparison = when.all[1]
    assert isinstance(comparison, ComparisonCondition)
    assert isinstance(comparison.right, LiteralOperand)
    assert comparison.right.literal == "30"


def test_trailing_multiple_fails_when_trailing_is_disabled() -> None:
    """Trailing axes fail closed instead of inventing an ATR trailing policy."""
    base = _reference()
    cell = expand_parameter_grid(
        (
            ParameterAxis(
                target=SweepAxisTarget.EXITS,
                parameter="trailing_stop_multiple",
                values=("1", "2"),
            ),
        )
    )[0]
    with pytest.raises(ValueError, match="enabled ATR trailing"):
        apply_parameter_cell(base, cell, base_fingerprint=strategy_fingerprint(base))


def test_execution_axis_substitutes_max_entry_wait_bars() -> None:
    """Execution axes rewrite wait bars without changing product or timeframe."""
    base = _reference()
    derived = apply_parameter_cell(
        base,
        expand_parameter_grid(
            (
                ParameterAxis(
                    target=SweepAxisTarget.EXECUTION,
                    parameter="max_entry_wait_bars",
                    values=("1", "3"),
                ),
            )
        )[0],
        base_fingerprint=strategy_fingerprint(base),
    )
    assert derived.execution.max_entry_wait_bars == 1
    assert derived.instrument.product_id == base.instrument.product_id
    assert derived.timeframe == base.timeframe


def test_sizing_axis_includes_target_in_canonical_json() -> None:
    """Non-indicator axes serialize target so request fingerprints stay distinct."""
    axis = ParameterAxis(
        target=SweepAxisTarget.SIZING,
        parameter="risk_fraction",
        values=("0.01", "0.02"),
    )
    payload = axis.model_dump(mode="json")
    assert payload["target"] == "sizing"
    assert "indicator_id" not in payload
