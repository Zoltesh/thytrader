"""Portfolio combination math against hand-checked fixtures (ADR 0088).

Window [S, S+4h). Sleeve A: BTC-USDC 1h, 500 capital. Sleeve B: ETH-USDC 2h, 300 capital.
Capital 1000 with a 20% reserve, so 200 stays cash. Child equity points are stamped at
bar starts and mark that bar's close, so A's point at S lands on the grid at S+1h.

Grid S, S+1h, S+2h, S+3h, S+4h. Combined equity 1000, 1000, 1020, 1030, 1004.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from uuid import UUID

import pytest

from thytrader.backtest.models import BacktestResult, BacktestSummary, EquityPoint
from thytrader.backtest.submission import BacktestSubmissionRequest
from thytrader.market_data.models import Candle, DatasetTimeframe
from thytrader.portfolios.backtest import (
    PlannedSleeve,
    PortfolioBacktestPlan,
    PortfolioEquityPoint,
    downsample_curve,
    portfolio_backtest_fingerprint,
)
from thytrader.portfolios.combine import (
    BasketInput,
    PortfolioCombinationError,
    SleeveRun,
    basket_sources,
    combine_portfolio,
    pearson,
)
from thytrader.research.models import CostAssumptions

_S = datetime(2026, 1, 1, tzinfo=UTC)
_E = _S + timedelta(hours=4)
_HOUR = timedelta(hours=1)
_A = UUID("01a0f000-0000-7000-8000-00000000000a")
_B = UUID("01a0f000-0000-7000-8000-00000000000b")
_CONTEXT = Context(prec=64, rounding=ROUND_HALF_EVEN)


def _fp(character: str) -> str:
    """A syntactically valid fingerprint."""
    return "sha256:" + character * 64


def _planned(
    sleeve_id: UUID, product: str, timeframe: DatasetTimeframe, capital: str, weight: str
) -> PlannedSleeve:
    """One planned sleeve with a dated child submission."""
    return PlannedSleeve(
        sleeve_id=sleeve_id,
        strategy_id=sleeve_id,
        strategy_name=f"{product} {timeframe}",
        strategy_fingerprint=_fp("1" if product.startswith("BTC") else "2"),
        product_id=product,
        covered_product_ids=(product,),
        timeframe=timeframe,
        weight_fraction=weight,
        capital_quote=capital,
        submission=BacktestSubmissionRequest(
            strategy_fingerprint=_fp("1" if product.startswith("BTC") else "2"),
            dataset_fingerprint=_fp("3" if product.startswith("BTC") else "4"),
            evaluation_start=_S,
            evaluation_end=_E,
            initial_quote_balance=capital,
            maker_fee_rate="0",
            taker_fee_rate="0",
            fixed_slippage_bps="0",
        ),
    )


def _plan(*sleeves: PlannedSleeve) -> PortfolioBacktestPlan:
    """A 1000 USDC plan with a 20% reserve and zero costs."""
    return PortfolioBacktestPlan(
        portfolio_id=UUID("01a0f000-0000-7000-8000-000000000001"),
        portfolio_revision=4,
        portfolio_name="Core",
        mode="paper",
        quote_currency="USDC",
        capital_quote="1000",
        cash_reserve_fraction="0.2",
        evaluation_start=_S,
        evaluation_end=_E,
        costs=CostAssumptions(maker_fee_rate="0", taker_fee_rate="0", fixed_slippage_bps="0"),
        sleeves=sleeves,
    )


def _point(at: datetime, cash: str, base: str, mark: str, equity: str) -> EquityPoint:
    """One child equity point."""
    return EquityPoint(
        candle_starts_at=at, cash=cash, base_quantity=base, mark_price=mark, equity=equity
    )


def _result(
    planned: PlannedSleeve, points: tuple[EquityPoint, ...], *, final: str, ret: str, trades: int
) -> BacktestResult:
    """A child result with the given curve (summary fields that combination reads)."""
    initial = planned.capital_quote
    return BacktestResult(
        schema_version="1.0",
        run_fingerprint=_fp("5"),
        strategy_fingerprint=planned.strategy_fingerprint,
        dataset_fingerprint=planned.submission.dataset_fingerprint,
        signal_trace_fingerprint=_fp("6"),
        trades=(),
        equity_curve=points,
        summary=BacktestSummary(
            initial_equity=initial,
            final_equity=final,
            total_net_pnl=str(Decimal(final) - Decimal(initial)),
            total_return_fraction=ret,
            gross_profit="0",
            gross_loss="0",
            win_rate="0",
            trade_count=trades,
            winning_trade_count=0,
            maximum_drawdown="0",
            maximum_drawdown_fraction="0.05",
            exposure_bars=2,
            evaluation_bars=len(points) - 1,
        ),
    )


def _runs() -> tuple[PortfolioBacktestPlan, tuple[SleeveRun, ...]]:
    """The two-sleeve fixture described in the module docstring."""
    first = _planned(_A, "BTC-USDC", "1h", "500", "0.5")
    second = _planned(_B, "ETH-USDC", "2h", "300", "0.3")
    first_result = _result(
        first,
        (
            _point(_S, "500", "0", "100", "500"),
            _point(_S + _HOUR, "0", "1", "510", "510"),
            _point(_S + 2 * _HOUR, "0", "1", "520", "520"),
            _point(_S + 3 * _HOUR, "515", "0", "515", "515"),
            _point(_E, "515", "0", "515", "515"),
        ),
        final="515",
        ret="0.03",
        trades=1,
    )
    second_result = _result(
        second,
        (
            _point(_S, "0", "0.1", "3100", "310"),
            _point(_S + 2 * _HOUR, "0", "0.1", "2900", "290"),
            _point(_E, "289", "0", "2890", "289"),
        ),
        final="289",
        ret="-0.0366666666666666666666666666666666666666666666666666666666666667",
        trades=2,
    )
    plan = _plan(first, second)
    return plan, (
        SleeveRun(
            planned=first,
            run_fingerprint=_fp("7"),
            result_fingerprint=_fp("8"),
            result=first_result,
        ),
        SleeveRun(
            planned=second,
            run_fingerprint=_fp("9"),
            result_fingerprint=_fp("a"),
            result=second_result,
        ),
    )


def _candles(
    start: datetime, step: timedelta, opens: tuple[str, ...], closes: tuple[str, ...]
) -> tuple[Candle, ...]:
    """Candles with the given opens and closes."""
    return tuple(
        Candle(
            starts_at=start + step * index,
            open=Decimal(open_),
            high=max(Decimal(open_), Decimal(close)) + 1,
            low=min(Decimal(open_), Decimal(close)) - 1,
            close=Decimal(close),
            volume=Decimal(10),
        )
        for index, (open_, close) in enumerate(zip(opens, closes, strict=True))
    )


def _basket(plan: PortfolioBacktestPlan) -> tuple[BasketInput, ...]:
    """BTC on 1h (opens 100.., closes 101..) and ETH on 2h (opens 50, 49, 47)."""
    sources = {source.product_id: source for source in basket_sources(plan)}
    return (
        BasketInput(
            source=sources["BTC-USDC"],
            candles=_candles(
                _S, _HOUR, ("100", "101", "102", "103", "105"), ("101", "102", "103", "104", "106")
            ),
        ),
        BasketInput(
            source=sources["ETH-USDC"],
            candles=_candles(_S, 2 * _HOUR, ("50", "49", "47"), ("49", "48", "46")),
        ),
    )


def test_combines_on_the_union_grid_with_forward_fill_and_cash() -> None:
    """Every number below is worked out by hand in the module docstring."""
    plan, runs = _runs()
    result = combine_portfolio(plan, runs, _basket(plan))
    assert [point.at for point in result.equity_curve] == [_S + _HOUR * hour for hour in range(5)]
    assert [point.equity for point in result.equity_curve] == [
        "1000",
        "1000",
        "1020",
        "1030",
        "1004",
    ]
    summary = result.summary
    assert summary.final_equity == "1004"
    assert summary.total_return_fraction == "0.004"
    assert summary.cash_quote == "200"
    assert summary.allocated_fraction == "0.8"
    assert summary.maximum_drawdown == "26"
    with localcontext(_CONTEXT):
        assert Decimal(summary.maximum_drawdown_fraction) == Decimal(26) / Decimal(1030)
        idle = (2 + Decimal(200) / Decimal(1020) + Decimal(200) / Decimal(1030)) / 4
    assert Decimal(summary.idle_capital_fraction) == idle
    assert summary.trade_count == 3
    assert summary.best_sleeve_return_fraction == "0.03"
    contributions = [Decimal(sleeve.contribution_fraction) for sleeve in result.sleeves]
    assert contributions == [Decimal("0.015"), Decimal("-0.011")]
    assert sum(contributions) == Decimal(summary.total_return_fraction)


def test_overlap_counts_time_long_together_and_on_the_same_asset() -> None:
    """A and B are both long for 2 of 4 hours, on different assets."""
    plan, runs = _runs()
    result = combine_portfolio(plan, runs, _basket(plan))
    assert result.overlap.long_together_fraction == "0.5"
    assert result.overlap.same_asset_fraction == "0"
    assert result.overlap.pairs == ()
    assert [sleeve.long_fraction for sleeve in result.sleeves] == ["0.5", "0.5"]


def test_correlation_needs_three_returns_on_the_common_clock() -> None:
    """1h and 2h sleeves share a 2h clock: S, S+2h, S+4h gives only two returns."""
    plan, runs = _runs()
    result = combine_portfolio(plan, runs, _basket(plan))
    assert result.correlation.return_clock_seconds == 7200
    assert result.correlation.observations == 2
    assert result.correlation.pairs[0].coefficient is None
    assert result.sleeves[0].correlation_to_rest is None


def test_pearson_against_hand_values() -> None:
    """Perfect, inverse, undefined, and 0.8 correlations."""
    values = [Decimal(1), Decimal(2), Decimal(3)]
    assert pearson(values, [Decimal(2), Decimal(4), Decimal(6)]) == 1
    assert pearson(values, [Decimal(6), Decimal(4), Decimal(2)]) == -1
    assert pearson(values, [Decimal(5)] * 3) is None
    assert pearson(values[:2], values[:2]) is None
    four = [Decimal(1), Decimal(2), Decimal(3), Decimal(4)]
    assert pearson(four, [Decimal(1), Decimal(3), Decimal(2), Decimal(4)]) == Decimal("0.8")


def test_equal_weight_basket_buys_each_primary_product_with_the_same_cash() -> None:
    """400 per asset: BTC 4 units 100 → 105, ETH 8 units 50 → 47; reserve 200 stays cash."""
    plan, runs = _runs()
    result = combine_portfolio(plan, runs, _basket(plan))
    basket = result.basket
    assert [(leg.product_id, leg.quantity, leg.return_fraction) for leg in basket.legs] == [
        ("BTC-USDC", "4", "0.05"),
        ("ETH-USDC", "8", "-0.06"),
    ]
    assert basket.final_equity == "996"
    assert basket.total_return_fraction == "-0.004"
    assert [point.basket_equity for point in result.equity_curve] == [
        "1000",
        "1004",
        "1000",
        "1004",
        "996",
    ]
    with localcontext(_CONTEXT):
        assert Decimal(basket.maximum_drawdown_fraction) == Decimal(8) / Decimal(1004)
    assert basket.total_fees == "0"


def test_result_fingerprint_is_stable_and_disclosures_are_honest() -> None:
    """Identical inputs give identical bytes; the independence caveat always ships."""
    plan, runs = _runs()
    first = combine_portfolio(plan, runs, _basket(plan))
    second = combine_portfolio(plan, runs, _basket(plan))
    assert portfolio_backtest_fingerprint(first) == portfolio_backtest_fingerprint(second)
    assert first.disclosures[0] == (
        "Sleeves simulated independently on fixed capital slices; portfolio-level caps and "
        "cross-sleeve interactions are not simulated."
    )


def test_refuses_child_results_that_do_not_match_the_plan() -> None:
    """A child over another window or capital cannot be combined."""
    plan, runs = _runs()
    shifted = runs[0].result.model_copy(update={"equity_curve": runs[0].result.equity_curve[:-1]})
    broken = (
        SleeveRun(
            planned=runs[0].planned,
            run_fingerprint=_fp("7"),
            result_fingerprint=_fp("8"),
            result=shifted,
        ),
        runs[1],
    )
    with pytest.raises(PortfolioCombinationError, match="evaluation window"):
        combine_portfolio(plan, broken, _basket(plan))
    with pytest.raises(PortfolioCombinationError, match="exactly one child"):
        combine_portfolio(plan, runs[:1], _basket(plan))


def test_downsample_keeps_endpoints_and_extremes() -> None:
    """Display thinning keeps the first, last, lowest, and highest points."""
    points = tuple(
        PortfolioEquityPoint(
            at=_S + _HOUR * index,
            equity=str(100 + (index % 7) * (-1) ** index),
            basket_equity="100",
        )
        for index in range(200)
    )
    thinned = downsample_curve(points, 50)
    assert len(thinned) <= 50
    assert thinned[0] == points[0]
    assert thinned[-1] == points[-1]
    equities = [Decimal(point.equity) for point in points[1:-1]]
    kept = {Decimal(point.equity) for point in thinned}
    assert min(equities) in kept
    assert max(equities) in kept
    assert downsample_curve(points[:10], 50) == points[:10]
