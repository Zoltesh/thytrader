"""Backtest entry-funnel diagnostics, take_profit none, and fingerprint stability (ADR 0090)."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import ValidationError
import pytest

from tests.backtest.test_kernel import (
    _SIGNAL,
    _WARMUP,
    _bars,
    _candles,
    _run,
    _short_strategy,
    _strategy,
    _with,
)
from thytrader.backtest.kernel import simulate_backtest, simulate_backtest_with_diagnostics
from thytrader.backtest.models import (
    BacktestDiagnostics,
    BacktestGateReason,
    BacktestSkipCount,
    backtest_result_fingerprint,
    canonical_backtest_diagnostics_bytes,
)
from thytrader.execution.geometry import EntrySkipReason
from thytrader.research.models import CapitalAssumptions, CostAssumptions
from thytrader.strategies.models import strategy_fingerprint

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import StrategyDefinition

# Captured from origin/main (fe77869) before ADR 0090: identities must not move.
_GOLDEN_LONG_STRATEGY = "sha256:6a55b94d967ad01ed47ae731712c1cb651ad29d6eeba012b8c935f60d56427a4"
_GOLDEN_LONG_RESULT = "sha256:8934559b0ca6ed4edf23a56fcf10c36ce139ade813b9c788967b851e6640d24c"
_GOLDEN_SHORT_TP_RESULT = "sha256:2b1ff39a7c394a8835e93292212063fab620529dc068401839df1c890688115b"
_GOLDEN_REPRICE_RESULT = "sha256:0f8e7078fa03e8a28d29841b3a40da5ab63b9ce48334690217ffd8cde12b7ed2"


def _short_with_take_profit(take_profit: dict[str, str]) -> StrategyDefinition:
    """The kernel short fixture with one take-profit declaration."""
    strategy = _short_strategy()
    exits = strategy.model_dump(mode="python")["exits"]
    exits["take_profit"] = take_profit
    return _with(strategy, exits=exits)


def _falling_after_signal() -> tuple[Candle, ...]:
    """Signal at 02:00, short fill at 03:00, a crash at 04:00, terminal bar at 05:00."""
    return _bars(
        *_WARMUP,
        _SIGNAL,
        ("14", "15", "13.5", "14"),
        ("10", "11", "1", "5"),
        ("5", "6", "4", "5"),
    )


def _skipped(diagnostics: BacktestDiagnostics) -> dict[str, int]:
    """Skip counts keyed by reason code."""
    return {item.reason.value: item.count for item in diagnostics.skipped}


def test_existing_result_fingerprints_are_unchanged() -> None:
    """Diagnostics live outside canonical bytes: pre-ADR-0090 results keep their identity."""
    long_strategy = _strategy()
    assert strategy_fingerprint(long_strategy) == _GOLDEN_LONG_STRATEGY
    long_result = simulate_backtest(_run(long_strategy), long_strategy, _candles())
    assert backtest_result_fingerprint(long_result) == _GOLDEN_LONG_RESULT
    short = _short_strategy()
    short_result = simulate_backtest(
        _run(short, evaluation_hours=3), short, _falling_after_signal()
    )
    assert backtest_result_fingerprint(short_result) == _GOLDEN_SHORT_TP_RESULT
    reprice = _with(
        long_strategy,
        execution={
            "entry_preference": "maker_only",
            "max_entry_wait_bars": 2,
            "on_unfilled_entry": "reprice",
        },
    )
    reprice_candles = _bars(
        *_WARMUP,
        ("14", "15", "13", "14"),
        ("16", "17", "15", "16"),
        ("18", "19", "17", "18"),
        ("18", "19", "17.5", "18"),
        ("18", "19", "17.5", "18"),
    )
    reprice_result, reprice_diagnostics = simulate_backtest_with_diagnostics(
        _run(reprice, evaluation_hours=4), reprice, reprice_candles
    )
    assert backtest_result_fingerprint(reprice_result) == _GOLDEN_REPRICE_RESULT
    assert reprice_diagnostics.entries_repriced >= 1


def test_short_whose_target_would_be_non_positive_explains_its_zero_trades() -> None:
    """The silent-skip defect: a 10R short target below zero now counts every skip."""
    strategy = _short_with_take_profit({"kind": "reward_risk", "multiple": "10"})
    result, diagnostics = simulate_backtest_with_diagnostics(
        _run(strategy, evaluation_hours=3), strategy, _falling_after_signal()
    )
    assert result.trades == ()
    assert diagnostics.signals_matched == 2
    assert diagnostics.entries_rested == 0
    assert _skipped(diagnostics) == {"target_not_positive": 2}


def test_take_profit_none_trades_the_same_signal_and_never_exits_on_a_target() -> None:
    """With no TP the same short enters, rides the crash, and exits at the window end."""
    strategy = _short_with_take_profit({"kind": "none"})
    result, diagnostics = simulate_backtest_with_diagnostics(
        _run(strategy, evaluation_hours=3), strategy, _falling_after_signal()
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit.reason == "evaluation_end"
    assert trade.exit.price.startswith("5")
    assert diagnostics.signals_matched == 2
    assert diagnostics.entries_rested == 1
    assert diagnostics.entries_filled == 1
    assert _skipped(diagnostics) == {"in_position": 1}


def test_take_profit_none_still_stops_out_on_the_fill_bar() -> None:
    """The stop protects a no-TP book exactly as it protects a bracketed one."""
    strategy = _short_with_take_profit({"kind": "none"})
    candles = _bars(*_WARMUP, _SIGNAL, ("14", "40", "13", "20"), ("20", "21", "19", "20"))
    result = simulate_backtest(_run(strategy), strategy, candles)
    assert [trade.exit.reason for trade in result.trades] == ["stop_loss"]


def test_expired_unfilled_entries_and_warmup_are_counted() -> None:
    """A limit that never trades through expires; the funnel says so."""
    strategy = _strategy()
    candles = _bars(
        *_WARMUP, ("14", "15", "13", "14"), ("16", "17", "15", "16"), ("18", "19", "17", "18")
    )
    result, diagnostics = simulate_backtest_with_diagnostics(_run(strategy), strategy, candles)
    assert result.trades == ()
    assert diagnostics.entries_rested >= 1
    assert diagnostics.entries_filled == 0
    assert (
        diagnostics.entries_expired + diagnostics.entries_unfilled_at_end
        == diagnostics.entries_rested
    )


def test_diagnostics_reject_an_incoherent_funnel() -> None:
    """Counts that do not add up are refused rather than stored."""
    with pytest.raises(ValidationError):
        BacktestDiagnostics(
            signals_matched=3,
            entries_rested=1,
            entries_filled=1,
            entries_expired=0,
            entries_repriced=0,
            entries_refused_at_fill=0,
            entries_unfilled_at_end=0,
            entries_size_capped=0,
            warmup_bars=0,
            skipped=(BacktestSkipCount(reason=BacktestGateReason.COOLDOWN, count=1),),
        )
    valid = BacktestDiagnostics(
        signals_matched=3,
        entries_rested=1,
        entries_filled=1,
        entries_expired=0,
        entries_repriced=0,
        entries_refused_at_fill=0,
        entries_unfilled_at_end=0,
        entries_size_capped=0,
        warmup_bars=4,
        skipped=(
            BacktestSkipCount(reason=BacktestGateReason.COOLDOWN, count=1),
            BacktestSkipCount(reason=EntrySkipReason.TARGET_NOT_POSITIVE, count=1),
        ),
    )
    encoded = canonical_backtest_diagnostics_bytes(valid)
    assert BacktestDiagnostics.model_validate_json(encoded) == valid
    assert b'"diagnostics_version":"thytrader-backtest-diagnostics-v1"' in encoded


@pytest.mark.parametrize(
    ("capital", "maker_fee_rate"),
    [("14.13", "0.005"), ("15.04", "0.001"), ("15.11", "0.001"), ("15.18", "0.001")],
)
def test_cash_capped_entries_always_fund_at_fill(capital: str, maker_fee_rate: str) -> None:
    """An entry sized to the whole fee-adjusted balance fills; rounding never refuses it.

    These balances were refused at fill before the cash bound kept headroom: the fill
    re-derived notional from ``notional / price`` and its last digit overshot the cash.
    """
    base = _strategy()
    exits = base.model_dump(mode="python")["exits"]
    exits["initial_stop"]["multiple"] = "0.5"
    strategy = _with(
        base,
        exits=exits,
        sizing={
            "kind": "risk_fraction",
            "risk_fraction": "0.25",
            "min_quote_notional": "1",
            "max_quote_notional": "1000",
        },
    )
    run = _run(strategy).model_copy(
        update={
            "capital": CapitalAssumptions(quote_currency="USD", initial_quote_balance=capital),
            "costs": CostAssumptions(
                maker_fee_rate=maker_fee_rate, taker_fee_rate="0.009", fixed_slippage_bps="5"
            ),
        }
    )
    result, diagnostics = simulate_backtest_with_diagnostics(run, strategy, _candles())

    assert diagnostics.entries_size_capped == 1
    assert diagnostics.entries_refused_at_fill == 0
    assert diagnostics.entries_filled == 1
    entry = result.trades[0].entry
    assert Decimal(entry.notional) + Decimal(entry.fee) <= Decimal(capital)
