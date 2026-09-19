"""Derived ratio metrics from an immutable backtest equity curve and trades.

These numbers are a versioned view of an existing result. They are not part of
the canonical simulation ledger and must not change v1/v2/v3 result fingerprints.

Formulas (risk-free rate 0):

- Bar returns are ``equity[i] / equity[i-1] - 1`` on consecutive equity points.
- Annualization uses the median positive equity-curve timestamp delta as the bar
  clock: ``bars_per_year = 365 * 24 * 3600 / bar_seconds``.
- Sharpe = mean(r) / sample_std(r) * sqrt(bars_per_year). Undefined when the
  sample std is 0 and mean is not 0.
- Sortino uses the sample std of strictly negative bar returns.
- CAGR = (final/initial) ** (1/years) - 1 with years from first-to-last equity
  timestamp over a 365-day year.
- Calmar = CAGR / abs(maximum_drawdown_fraction).
- SQN = sqrt(n) * mean(R) / sample_std(R) where R is trade net_pnl / initial equity.
- Buy-and-hold return is last_mark / first_mark - 1 (gross, no fees).
"""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise
from statistics import median
from typing import TYPE_CHECKING

from thytrader.backtest.models import BacktestPerformanceMetrics, backtest_result_fingerprint
from thytrader.research.indicators import canonical_decimal

if TYPE_CHECKING:
    from thytrader.backtest.models import BacktestResult, BacktestTrade, EquityPoint

_SECONDS_PER_YEAR = Decimal("31536000")
_ZERO = Decimal("0")


def compute_performance_metrics(result: BacktestResult) -> BacktestPerformanceMetrics:
    """Compute thytrader-performance-metrics-v1 from one reverified result."""
    returns = _bar_returns(result.equity_curve)
    bar_seconds = _median_bar_seconds(result.equity_curve)
    bars_per_year = (
        None if bar_seconds is None or bar_seconds <= 0 else _SECONDS_PER_YEAR / bar_seconds
    )
    mean_r = _mean(returns)
    std_r = _sample_std(returns)
    downside = tuple(item for item in returns if item < 0)
    initial = Decimal(result.summary.initial_equity)
    cagr = _cagr(result)
    drawdown = Decimal(result.summary.maximum_drawdown_fraction)
    trade_returns = (
        tuple(Decimal(trade.net_pnl) / initial for trade in result.trades) if initial else ()
    )
    first_mark = Decimal(result.equity_curve[0].mark_price)
    last_mark = Decimal(result.equity_curve[-1].mark_price)
    buy_hold = None if first_mark == 0 else last_mark / first_mark - 1
    return BacktestPerformanceMetrics(
        metrics_contract_version="thytrader-performance-metrics-v1",
        result_fingerprint=backtest_result_fingerprint(result),
        run_fingerprint=result.run_fingerprint,
        engine_contract_version=result.engine_contract_version,
        risk_free_rate="0",
        annualization="equity_curve_bar_clock",
        bar_seconds=_optional_decimal(bar_seconds),
        bars_per_year=_optional_decimal(bars_per_year),
        sharpe=_optional_decimal(_annualized_ratio(mean_r, std_r, bars_per_year)),
        sortino=_optional_decimal(_annualized_ratio(mean_r, _sample_std(downside), bars_per_year)),
        calmar=_optional_decimal(_calmar(cagr, drawdown)),
        sqn=_optional_decimal(_sqn(trade_returns)),
        cagr=_optional_decimal(cagr),
        annualized_volatility=_optional_decimal(_annualized_vol(std_r, bars_per_year)),
        max_consecutive_losses=_max_consecutive_losses(result.trades),
        exposure_fraction=canonical_decimal(
            Decimal(result.summary.exposure_bars) / Decimal(result.summary.evaluation_bars)
        ),
        buy_and_hold_return_fraction=_optional_decimal(buy_hold),
    )


def _bar_returns(curve: tuple[EquityPoint, ...]) -> tuple[Decimal, ...]:
    """Return period returns between consecutive equity marks."""
    returns: list[Decimal] = []
    for previous, current in pairwise(curve):
        start = Decimal(previous.equity)
        if start == 0:
            continue
        returns.append(Decimal(current.equity) / start - 1)
    return tuple(returns)


def _median_bar_seconds(curve: tuple[EquityPoint, ...]) -> Decimal | None:
    """Return the median positive timestamp delta on the equity curve."""
    deltas = [
        (current.candle_starts_at - previous.candle_starts_at).total_seconds()
        for previous, current in pairwise(curve)
        if current.candle_starts_at > previous.candle_starts_at
    ]
    if not deltas:
        return None
    return Decimal(str(median(deltas)))


def _mean(values: tuple[Decimal, ...]) -> Decimal | None:
    """Return the arithmetic mean, or None when empty."""
    if not values:
        return None
    return sum(values, start=_ZERO) / Decimal(len(values))


def _sample_std(values: tuple[Decimal, ...]) -> Decimal | None:
    """Return Bessel-corrected sample standard deviation."""
    if len(values) < 2:
        return None
    mean = _mean(values)
    if mean is None:
        return None
    variance = sum((item - mean) ** 2 for item in values) / Decimal(len(values) - 1)
    return variance.sqrt()


def _annualized_ratio(
    mean: Decimal | None,
    std: Decimal | None,
    bars_per_year: Decimal | None,
) -> Decimal | None:
    """Annualize mean/std at rf=0, or 0 when both mean and std are 0."""
    if mean is None or std is None or bars_per_year is None:
        return None
    if std == 0:
        return _ZERO if mean == 0 else None
    return (mean / std) * bars_per_year.sqrt()


def _annualized_vol(std: Decimal | None, bars_per_year: Decimal | None) -> Decimal | None:
    """Annualize bar-return volatility."""
    if std is None or bars_per_year is None:
        return None
    return std * bars_per_year.sqrt()


def _cagr(result: BacktestResult) -> Decimal | None:
    """Compound annual growth from first-to-last equity timestamps."""
    initial = Decimal(result.summary.initial_equity)
    final = Decimal(result.summary.final_equity)
    if initial <= 0:
        return None
    elapsed = result.equity_curve[-1].candle_starts_at - result.equity_curve[0].candle_starts_at
    years = Decimal(str(elapsed.total_seconds())) / _SECONDS_PER_YEAR
    if years <= 0:
        return None
    return (final / initial) ** (Decimal("1") / years) - 1


def _calmar(cagr: Decimal | None, drawdown_fraction: Decimal) -> Decimal | None:
    """CAGR divided by absolute maximum drawdown fraction."""
    if cagr is None or drawdown_fraction <= 0:
        return None
    return cagr / drawdown_fraction


def _sqn(trade_returns: tuple[Decimal, ...]) -> Decimal | None:
    """Van Tharp SQN from per-trade return fractions."""
    std = _sample_std(trade_returns)
    mean = _mean(trade_returns)
    if mean is None or std is None or std == 0:
        return None
    return Decimal(len(trade_returns)).sqrt() * mean / std


def _max_consecutive_losses(trades: tuple[BacktestTrade, ...]) -> int:
    """Longest run of closed trades with negative net PnL."""
    streak = 0
    longest = 0
    for trade in trades:
        if Decimal(trade.net_pnl) < 0:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0
    return longest


def _optional_decimal(value: Decimal | None) -> str | None:
    """Canonicalize a finite metric or omit it when undefined."""
    if value is None:
        return None
    return canonical_decimal(value)
