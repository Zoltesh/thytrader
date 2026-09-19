"""Derived performance metrics must not change canonical result identity."""

from __future__ import annotations

from datetime import UTC, datetime

from thytrader.backtest.metrics import compute_performance_metrics
from thytrader.backtest.models import (
    BacktestExitFill,
    BacktestFill,
    BacktestResult,
    BacktestSummary,
    BacktestTrade,
    EquityPoint,
    backtest_result_fingerprint,
    canonical_backtest_result_bytes,
)


def _point(hour: int, equity: str, mark: str) -> EquityPoint:
    """One hourly equity mark."""
    return EquityPoint(
        candle_starts_at=datetime(2026, 1, 1, hour, tzinfo=UTC),
        cash=equity,
        base_quantity="0",
        mark_price=mark,
        equity=equity,
    )


def _fill(hour: int, price: str, pnl_fee: str = "0") -> BacktestFill:
    """One modeled fill at an hourly open."""
    return BacktestFill(
        candle_starts_at=datetime(2026, 1, 1, hour, tzinfo=UTC),
        price=price,
        quantity="1",
        notional=price,
        fee=pnl_fee,
        fee_rate="0",
    )


def _trade(*, entry_hour: int, exit_hour: int, net_pnl: str) -> BacktestTrade:
    """One closed long with the requested net PnL."""
    return BacktestTrade(
        entry=_fill(entry_hour, "100"),
        exit=BacktestExitFill(
            candle_starts_at=datetime(2026, 1, 1, exit_hour, tzinfo=UTC),
            price="100",
            quantity="1",
            notional="100",
            fee="0",
            fee_rate="0",
            reason="time_exit",
        ),
        gross_pnl=net_pnl,
        net_pnl=net_pnl,
        holding_bars=max(1, exit_hour - entry_hour),
    )


def _result(
    *, equity: tuple[EquityPoint, ...], trades: tuple[BacktestTrade, ...]
) -> BacktestResult:
    """Build a V1 result whose summary matches the supplied ledger facts."""
    initial = equity[0].equity
    final = equity[-1].equity
    fingerprint = "sha256:" + "a" * 64
    wins = tuple(
        trade for trade in trades if not trade.net_pnl.startswith("-") and trade.net_pnl != "0"
    )
    return BacktestResult(
        schema_version="1.0",
        engine_contract_version="thytrader-bar-backtest-v1",
        run_fingerprint=fingerprint,
        strategy_fingerprint=fingerprint,
        dataset_fingerprint=fingerprint,
        signal_trace_fingerprint=fingerprint,
        trades=trades,
        equity_curve=equity,
        summary=BacktestSummary(
            initial_equity=initial,
            final_equity=final,
            total_net_pnl="0",
            total_return_fraction="0",
            gross_profit="0",
            gross_loss="0",
            win_rate="0",
            trade_count=len(trades),
            winning_trade_count=len(wins),
            maximum_drawdown="10",
            maximum_drawdown_fraction="0.1",
            exposure_bars=5,
            evaluation_bars=10,
        ),
    )


def test_metrics_leave_canonical_result_bytes_unchanged() -> None:
    """Computing Sharpe must not alter v1/v2/v3 result identity."""
    result = _result(
        equity=(_point(0, "100", "10"), _point(1, "110", "11"), _point(2, "90", "9")),
        trades=(),
    )
    before = canonical_backtest_result_bytes(result)
    fingerprint = backtest_result_fingerprint(result)
    compute_performance_metrics(result)
    assert canonical_backtest_result_bytes(result) == before
    assert backtest_result_fingerprint(result) == fingerprint


def test_zero_mean_return_has_zero_sharpe() -> None:
    """Equal up and down bar returns produce Sharpe 0 at rf=0."""
    result = _result(
        equity=(_point(0, "100", "10"), _point(1, "110", "11"), _point(2, "99", "9.9")),
        trades=(),
    )
    metrics = compute_performance_metrics(result)
    assert metrics.metrics_contract_version == "thytrader-performance-metrics-v1"
    assert metrics.sharpe == "0"
    assert metrics.risk_free_rate == "0"
    assert metrics.annualization == "equity_curve_bar_clock"
    assert metrics.bars_per_year == "8760"
    assert metrics.exposure_fraction == "0.5"


def test_max_consecutive_losses_counts_trade_streak() -> None:
    """Consecutive losing trades are counted from the closed-trade sequence."""
    result = _result(
        equity=(_point(0, "100", "10"), _point(3, "100", "10")),
        trades=(
            _trade(entry_hour=0, exit_hour=1, net_pnl="-1"),
            _trade(entry_hour=1, exit_hour=2, net_pnl="-2"),
            _trade(entry_hour=2, exit_hour=3, net_pnl="3"),
        ),
    )
    metrics = compute_performance_metrics(result)
    assert metrics.max_consecutive_losses == 2


def test_buy_and_hold_return_uses_first_and_last_marks() -> None:
    """Buy-and-hold is mark-to-mark from the equity curve, without fees."""
    result = _result(
        equity=(_point(0, "100", "10"), _point(1, "100", "12")),
        trades=(),
    )
    metrics = compute_performance_metrics(result)
    assert metrics.buy_and_hold_return_fraction == "0.2"
