"""Per-bar explanation pages reuse verified traces and immutable results (ADR 0116)."""

from tests.api.test_backtests import _candles, _run, _strategy
from thytrader.backtest.kernel import simulate_backtest_with_diagnostics
from thytrader.backtest.models import backtest_evaluation_window, backtest_result_fingerprint
from thytrader.evaluation.signal_evaluator import evaluate_signal_trace
from thytrader.research.bar_explanations import bar_explanation_page


def test_explanation_page_is_deterministic_and_joins_only_recorded_facts() -> None:
    """The same result explains the same bars, and empty bars invent no fills."""
    strategy = _strategy()
    specification = _run(strategy)
    candles = _candles()
    result, _diagnostics = simulate_backtest_with_diagnostics(specification, strategy, candles)
    trace = evaluate_signal_trace(specification, strategy, candles)
    window = backtest_evaluation_window(specification, result.summary.evaluation_bars)
    fingerprint = backtest_result_fingerprint(result)
    first = bar_explanation_page(
        trace,
        result,
        window=window,
        product_id=strategy.instrument.product_id,
        limit=100,
        offset=0,
        result_fingerprint=fingerprint,
        next_cursor_for=str,
    )
    second = bar_explanation_page(
        trace,
        result,
        window=window,
        product_id=strategy.instrument.product_id,
        limit=100,
        offset=0,
        result_fingerprint=fingerprint,
        next_cursor_for=str,
    )
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.total_bars == len(trace.records)
    recorded_entries = sum(len(record.entries) for record in first.records) + sum(
        1 for item in first.outside_trace if item.kind == "entry"
    )
    recorded_exits = sum(len(record.exits) for record in first.records) + sum(
        1 for item in first.outside_trace if item.kind == "exit"
    )
    assert recorded_entries == recorded_exits == len(result.trades)
    assert any(
        item.kind == "exit" and getattr(item.fill, "reason", None) == "evaluation_end"
        for item in first.outside_trace
    )
    quiet = [record for record in first.records if not record.entries and not record.exits]
    assert quiet
    assert all(record.equity is None or record.equity.equity for record in first.records)
