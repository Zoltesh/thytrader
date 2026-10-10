"""Futures simulation in the unified backtest kernel (ADR 0128, slice P1-2).

Every expected number is computed by hand from the fixture: ATR(2) at the 02:00 signal bar
is 3, the signal close (and resting limit) is 14, capital is 10000 USD, the maker fee is
0.001, the taker fee 0.002, slippage 10 bps, and the per-contract fee 0.01.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest

from tests.backtest.test_kernel import _candles, _run, _short_strategy, _strategy as _spot_strategy
from thytrader.backtest.kernel import (
    BacktestSimulationError,
    simulate_backtest,
    simulate_backtest_with_diagnostics,
)
from thytrader.backtest.kernel_futures import _size_contracts
from thytrader.backtest.kernel_state import _FuturesTerms
from thytrader.backtest.models import (
    BacktestGateReason,
    backtest_result_fingerprint,
    canonical_backtest_result_bytes,
)
from thytrader.evaluation.futures_spec import funding_series_fingerprint
from thytrader.evaluation.models import ResearchRunSpecification
from thytrader.market_data.models import Candle
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.trading.geometry import EntrySkipReason

_PERP = "BIP-20DEC30-CDE"
_DATED = "BIT-01AUG26-CDE"
_START = datetime(2026, 8, 1, tzinfo=UTC)
_EVAL = _START + timedelta(hours=2)
_WARMUP = (("10", "11", "9", "10"), ("11", "12", "10", "11"))
_SIGNAL = ("14", "15", "12", "14")


def _hour(value: int) -> datetime:
    """One hourly UTC boundary on the fixture day."""
    return _START + timedelta(hours=value)


def _strategy(
    *,
    product_id: str = _PERP,
    side: str = "long",
    risk_fraction: str = "0.01",
    stop_multiple: str = "2",
    max_leverage: str = "2",
    flatten_hours: int | None = None,
) -> StrategyDefinition:
    """The kernel fixture strategy re-targeted at one futures contract."""
    payload: dict[str, Any] = json.loads(
        Path("tests/strategies/golden/reference_strategy_v1.json").read_text()
    )
    payload["instrument"] = {
        "product_id": product_id,
        "base_currency": "BTC",
        "quote_currency": "USD",
        "kind": "future",
    }
    derivatives: dict[str, Any] = {"max_leverage": max_leverage}
    if flatten_hours is not None:
        derivatives["flatten_before_expiry_hours"] = flatten_hours
    payload["derivatives"] = derivatives
    payload["data_requirements"] = {
        "warmup_bars": 2,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    payload["indicators"] = [
        {"id": "sma", "kind": "sma", "input": "close", "parameters": {"period": 2}},
        {
            "id": "atr",
            "kind": "atr",
            "input": ["high", "low", "close"],
            "parameters": {"period": 2},
        },
    ]
    payload["entry"] = {
        "side": side,
        "when": {
            "all": [
                {
                    "left": {"indicator": "sma"},
                    "operator": "greater_than",
                    "right": {"literal": "12"},
                }
            ]
        },
        "cooldown_bars": 0,
        "max_open_positions": 1,
    }
    payload["exits"] = {
        "initial_stop": {"kind": "atr_multiple", "atr_indicator": "atr", "multiple": stop_multiple},
        "take_profit": {"kind": "reward_risk", "multiple": "2"},
        "trailing_stop": {"enabled": False},
        "time_exit": {"max_bars_held": 96},
    }
    payload["sizing"] = {
        "kind": "risk_fraction",
        "risk_fraction": risk_fraction,
        "min_quote_notional": "1",
        "max_quote_notional": "100000",
    }
    payload["portfolio_limits"] = {
        "max_strategy_exposure_fraction": "1",
        "max_concurrent_positions": 1,
    }
    return StrategyDefinition.model_validate(payload)


def _spec(
    strategy: StrategyDefinition,
    *,
    hours: int = 2,
    contract: dict[str, Any] | None = None,
    margin: dict[str, Any] | None = None,
    funding: dict[str, Any] | None = None,
) -> ResearchRunSpecification:
    """A futures run over ``hours`` evaluated hourly bars from 02:00."""
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": "019cae99-3e00-7000-8000-000000000001",
        "created_at": "2026-03-02T12:50:04.416Z",
        "strategy_fingerprint": strategy_fingerprint(strategy),
        "dataset_fingerprint": "sha256:" + "a" * 64,
        "evaluation": {
            "starts_at": "2026-08-01T02:00:00Z",
            "ends_at": (_EVAL + timedelta(hours=hours)).isoformat().replace("+00:00", "Z"),
        },
        "warmup": {"bars": 2, "starts_at": "2026-08-01T00:00:00Z"},
        "capital": {"quote_currency": "USD", "initial_quote_balance": "10000"},
        "costs": {
            "maker_fee_rate": "0.001",
            "taker_fee_rate": "0.002",
            "fixed_slippage_bps": "10",
            "fee_per_contract": "0.01",
        },
        "random_seed": 0,
        "instrument_contract": contract
        or {
            "product_id": _PERP,
            "kind": "perpetual_future",
            "underlying": "BTC",
            "contract_size": "0.1",
            "expires_at": None,
            "listed_expiry": "2030-12-20",
            "catalog_fingerprint": "sha256:" + "c" * 64,
        },
        "margin": margin or {"long_rate": "0.2", "short_rate": "0.25", "source": "explicit"},
    }
    if funding is not None:
        payload["funding"] = funding
    elif (payload["instrument_contract"]["kind"]) == "perpetual_future":
        payload["funding"] = {"constant_rate": "0.0001"}
    return ResearchRunSpecification.model_validate_json(json.dumps(payload))


def _bars(*rows: tuple[str, str, str, str]) -> tuple[Candle, ...]:
    """Hourly candles from 00:00 UTC (two warmup bars precede evaluation)."""
    return tuple(
        Candle(
            starts_at=_hour(index),
            open=Decimal(open_),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
            volume=Decimal("10"),
        )
        for index, (open_, high, low, close) in enumerate(rows)
    )


_HOLD = _bars(*_WARMUP, _SIGNAL, ("14", "16", "13.5", "15"), ("15", "15", "15", "15"))


def test_long_perp_holds_whole_contracts_pays_funding_and_per_contract_fees() -> None:
    """166 contracts of 0.1 (risk-sized 233.33 USD) pay one funding hour at the 03:00 close.

    Entry: 16.6 x 14 = 232.4, fee 0.2324 + 166 x 0.01 = 1.8924. Funding at 04:00:
    -16.6 x 15 x 0.0001 = -0.0249. Exit at the 04:00 open as a taker: 15 x 0.999 = 14.985,
    notional 248.751, fee 0.497502 + 1.66 = 2.157502. Net: 16.351 - 1.8924 - 2.157502 -
    0.0249 = 12.276198.
    """
    strategy = _strategy()
    result = simulate_backtest(_spec(strategy), strategy, _HOLD)

    (trade,) = result.trades
    assert trade.entry.quantity == "16.6"
    assert trade.entry.notional == "232.4"
    assert trade.entry.fee == "1.8924"
    assert trade.exit.reason == "evaluation_end"
    assert trade.exit.price == "14.985"
    assert trade.exit.fee == "2.157502"
    assert trade.gross_pnl == "16.351"
    assert trade.funding == "-0.0249"
    assert trade.net_pnl == "12.276198"
    assert result.summary.final_equity == "10012.276198"
    assert result.summary.total_funding == "-0.0249"
    assert result.summary.validity_limits == (
        "maker_touch_full_fill",
        "stop_before_tp_same_bar",
        "futures_constant_margin",
        "futures_conservative_liquidation",
        "futures_shared_usdc_collateral",
        "futures_constant_funding",
    )


def test_short_perp_is_a_real_short_that_receives_positive_funding() -> None:
    """A short receives a positive rate and is never disclosed as a synthetic spot short.

    Entry 16.6 at 14 (fee 1.8924); funding +16.6 x 13 x 0.0001 = 0.02158; exit buy at
    13 x 1.001 = 13.013, notional 216.0158, fee 0.4320316 + 1.66 = 2.0920316. Net:
    16.3842 - 1.8924 - 2.0920316 + 0.02158 = 12.4213484.
    """
    strategy = _strategy(side="short")
    candles = _bars(*_WARMUP, _SIGNAL, ("14", "14.5", "13", "13"), ("13", "13", "13", "13"))
    result = simulate_backtest(_spec(strategy), strategy, candles)

    (trade,) = result.trades
    assert trade.entry.quantity == "16.6"
    assert trade.funding == "0.02158"
    assert trade.net_pnl == "12.4213484"
    assert result.summary.final_equity == "10012.4213484"
    assert "spot_short_synthetic" not in (result.summary.validity_limits or ())


def test_an_entry_below_one_contract_is_skipped_never_rounded_up() -> None:
    """A 100-unit contract costs 1400 USD; the 233 USD risk size is zero contracts."""
    strategy = _strategy()
    contract = {
        "product_id": _PERP,
        "kind": "perpetual_future",
        "underlying": "BTC",
        "contract_size": "100",
        "expires_at": None,
        "listed_expiry": "2030-12-20",
        "catalog_fingerprint": "sha256:" + "c" * 64,
    }
    result, diagnostics = simulate_backtest_with_diagnostics(
        _spec(strategy, contract=contract), strategy, _HOLD
    )

    assert result.trades == ()
    skipped = {item.reason: item.count for item in diagnostics.skipped}
    assert skipped[EntrySkipReason.BELOW_ONE_CONTRACT] == 2


def test_liquidation_is_checked_at_the_adverse_extreme_before_the_stop() -> None:
    """A 2.33x long is liquidated at the 8 low, though its stop (12.5) is touched first.

    Size: 0.25 x 10000 / 1.5 x 14 = 23333.33 USD -> 16666 contracts (1666.6). Cash after
    entry: 10000 - 23332.4 - 189.9924 = -13522.3924. At low 8 equity is -190.5924 against
    maintenance 666.64, so the position closes at 8 x 0.999 = 7.992 as a taker.
    """
    strategy = _strategy(risk_fraction="0.25", stop_multiple="0.5", max_leverage="3")
    margin = {
        "long_rate": "0.05",
        "short_rate": "0.05",
        "source": "explicit",
        "min_liquidation_buffer_fraction": "0",
    }
    candles = _bars(
        *_WARMUP,
        _SIGNAL,
        ("14", "14.5", "13.9", "14.2"),
        ("14", "14", "8", "9"),
        ("9", "9", "9", "9"),
    )
    spec = _spec(strategy, hours=3, margin=margin, funding={"constant_rate": "0"})
    result = simulate_backtest(spec, strategy, candles)

    (trade,) = result.trades
    assert trade.entry.quantity == "1666.6"
    assert trade.exit.reason == "liquidation"
    assert trade.exit.candle_starts_at == _hour(4)
    assert trade.exit.price == "7.992"
    assert trade.net_pnl == "-10396.2241344"
    assert result.summary.final_equity == "-396.2241344"


def test_dated_contract_flattens_before_expiry_and_rests_nothing_after() -> None:
    """Expiry 07:00 with a 2-hour flatten: the long closes at the 05:00 open as a taker."""
    strategy = _strategy(product_id=_DATED, flatten_hours=2)
    contract = {
        "product_id": _DATED,
        "kind": "dated_future",
        "underlying": "BTC",
        "contract_size": "0.1",
        "expires_at": "2026-08-01T07:00:00Z",
        "listed_expiry": "2026-08-01",
        "catalog_fingerprint": "sha256:" + "c" * 64,
    }
    candles = _bars(
        *_WARMUP,
        _SIGNAL,
        ("14", "16", "13.5", "15"),
        ("15", "16", "14.5", "15"),
        ("15", "15", "15", "15"),
        ("15", "15", "15", "15"),
        ("15", "15", "15", "15"),
    )
    result, diagnostics = simulate_backtest_with_diagnostics(
        _spec(strategy, hours=5, contract=contract), strategy, candles
    )

    (trade,) = result.trades
    assert trade.exit.reason == "expiry"
    assert trade.exit.candle_starts_at == _hour(5)
    assert trade.exit.price == "14.985"
    assert trade.funding is None
    assert result.summary.total_funding is None
    skipped = {item.reason: item.count for item in diagnostics.skipped}
    assert skipped[BacktestGateReason.EXPIRY_WINDOW] == 3


def test_dated_contract_window_and_flatten_rules_are_enforced() -> None:
    """A window past expiry, or a dated strategy without a flatten time, is refused."""
    contract = {
        "product_id": _DATED,
        "kind": "dated_future",
        "underlying": "BTC",
        "contract_size": "0.1",
        "expires_at": "2026-08-01T03:00:00Z",
        "listed_expiry": "2026-08-01",
        "catalog_fingerprint": "sha256:" + "c" * 64,
    }
    flattening = _strategy(product_id=_DATED, flatten_hours=1)
    with pytest.raises(BacktestSimulationError, match="FUTURES_WINDOW_PAST_EXPIRY"):
        simulate_backtest(_spec(flattening, contract=contract), flattening, _HOLD)
    unset = _strategy(product_id=_DATED)
    with pytest.raises(BacktestSimulationError, match="FUTURES_EXPIRY_UNSET"):
        simulate_backtest(_spec(unset, contract=contract), unset, _HOLD)


def _series(rates: dict[datetime, Decimal]) -> dict[str, Any]:
    """The run-spec binding of one recorded funding series."""
    return {
        "series_fingerprint": funding_series_fingerprint(_PERP, rates),
        "settled_hours": len(rates),
    }


def test_recorded_funding_charges_the_hour_that_closes_inside_the_bar() -> None:
    """Only 04:00 is charged (the position opens during the 03:00 bar): -16.6 x 15 x 0.0002."""
    strategy = _strategy()
    rates = {_hour(3): Decimal("0.0005"), _hour(4): Decimal("0.0002")}
    result = simulate_backtest(
        _spec(strategy, funding=_series(rates)), strategy, _HOLD, funding_rates=rates
    )

    assert result.trades[0].funding == "-0.0498"
    assert "futures_constant_funding" not in (result.summary.validity_limits or ())


def test_a_missing_or_mismatched_funding_hour_fails_the_run() -> None:
    """Every funding hour of the window must be supplied and match the bound fingerprint."""
    strategy = _strategy()
    partial = {_hour(3): Decimal("0.0005")}
    with pytest.raises(BacktestSimulationError, match=r"FUNDING_HISTORY_MISSING.*04:00:00Z"):
        simulate_backtest(
            _spec(strategy, funding=_series(partial)), strategy, _HOLD, funding_rates=partial
        )
    bound = {_hour(3): Decimal("0.0005"), _hour(4): Decimal("0.0002")}
    tampered = {**bound, _hour(4): Decimal("0.0003")}
    with pytest.raises(BacktestSimulationError, match="FUNDING_SERIES_MISMATCH"):
        simulate_backtest(
            _spec(strategy, funding=_series(bound)), strategy, _HOLD, funding_rates=tampered
        )
    with pytest.raises(BacktestSimulationError, match="FUNDING_HISTORY_MISSING"):
        simulate_backtest(_spec(strategy, funding=_series(bound)), strategy, _HOLD)


def test_futures_results_are_deterministic() -> None:
    """Two runs of the same futures inputs produce identical canonical bytes."""
    strategy = _strategy()
    first = simulate_backtest(_spec(strategy), strategy, _HOLD)
    second = simulate_backtest(_spec(strategy), strategy, _HOLD)
    assert canonical_backtest_result_bytes(first) == canonical_backtest_result_bytes(second)


def _terms(
    *, min_buffer_fraction: Decimal = Decimal("0.5"), max_leverage: Decimal = Decimal(2)
) -> _FuturesTerms:
    """Sizing terms: 0.1 contracts, 20% initial margin, 50% buffer, 2x leverage."""
    return _FuturesTerms(
        contract_size=Decimal("0.1"),
        long_rate=Decimal("0.2"),
        short_rate=Decimal("0.25"),
        maintenance_fraction=Decimal(1),
        min_buffer_fraction=min_buffer_fraction,
        max_leverage=max_leverage,
        fee_per_contract=Decimal("0.01"),
    )


@pytest.mark.parametrize(
    ("terms", "contracts"),
    [
        # Leverage: 2 x 10000 / (1.4 + 2 x 0.0114) = 14056.8 -> 14056.
        (_terms(), Decimal(14056)),
        # Buffer: 0.1 x 10000 / (1.4 x 0.2 + 0.1 x 0.0114) = 3556.9 -> 3556.
        (_terms(min_buffer_fraction=Decimal("0.9")), Decimal(3556)),
    ],
)
def test_contract_sizing_bounds_hold_after_the_entry_fee(
    terms: _FuturesTerms, contracts: Decimal
) -> None:
    """The binding bound sets whole contracts, and the result reports it as capped."""
    sized = _size_contracts(
        terms,
        _strategy(),
        cash=Decimal(10000),
        side="long",
        limit_price=Decimal(14),
        requested_notional=Decimal(1_000_000),
        maker_fee_rate=Decimal("0.001"),
    )
    assert sized == (contracts * Decimal("0.1"), True)


def test_contract_sizing_refuses_without_equity() -> None:
    """A book with no equity rests no futures entry."""
    sized = _size_contracts(
        _terms(),
        _strategy(),
        cash=Decimal(-1),
        side="long",
        limit_price=Decimal(14),
        requested_notional=Decimal(100),
        maker_fee_rate=Decimal("0.001"),
    )
    assert sized is EntrySkipReason.INSUFFICIENT_CASH


@pytest.mark.parametrize(
    ("name", "fingerprint"),
    [
        ("long", "sha256:8934559b0ca6ed4edf23a56fcf10c36ce139ade813b9c788967b851e6640d24c"),
        ("short", "sha256:c235eaaa2908c1f676e667d74b2fd75879539e0b95c94c2a6bea6aa55f1d810a"),
        ("spread", "sha256:edc0457d3a0c6804996852e6fd579b190393c1d44f8e37a4ecb2a8870988f50a"),
    ],
)
def test_spot_result_bytes_are_unchanged_by_futures_support(name: str, fingerprint: str) -> None:
    """Spot kernel fixtures keep the result fingerprints they had before P1-2."""
    strategy = _short_strategy() if name == "short" else _spot_strategy()
    spec = _run(strategy, spread_bps="20" if name == "spread" else None)
    result = simulate_backtest(spec, strategy, _candles())
    assert backtest_result_fingerprint(result) == fingerprint
