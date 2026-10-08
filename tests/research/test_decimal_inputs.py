"""Decimal request fields accept JSON numbers without changing any fingerprint (ADR 0094)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
import pytest

from thytrader.backtest.submission import (
    BacktestStartRequest,
    _execution_fingerprint,
)
from thytrader.evaluation.models import json_number_as_decimal_text
from thytrader.portfolios.backtest import PortfolioBacktestRequest
from thytrader.research.parameter_sweep import ParameterAxis
from thytrader.research.studies import ResearchStudyRequest, request_fingerprint
from thytrader.research.study_start import ResearchStudyStartRequest

_STRATEGY_ID = UUID("0199aaaa-aaaa-7aaa-aaaa-aaaaaaaaaaaa")
_STRATEGY = "sha256:" + "a" * 64
_DATASET = "sha256:" + "b" * 64
_START = datetime(2026, 1, 1, tzinfo=UTC)
_END = datetime(2026, 3, 1, tzinfo=UTC)


def _backtest(**costs: object) -> BacktestStartRequest:
    """Return one backtest start with the given cost fields."""
    return BacktestStartRequest.model_validate(
        {
            "strategy_id": str(_STRATEGY_ID),
            "dataset_fingerprint": _DATASET,
            "evaluation_start": "2026-01-01T00:00:00Z",
            "evaluation_end": "2026-03-01T00:00:00Z",
            **costs,
        }
    )


def test_numbers_and_strings_give_the_same_backtest_request_and_execution_fingerprint() -> None:
    """``fixed_slippage_bps: 5`` is the same run as ``"5"``: same request, same dedupe key."""
    numbers = _backtest(
        initial_quote_balance=10000,
        maker_fee_rate=0.004,
        taker_fee_rate=0.006,
        fixed_slippage_bps=5,
        spread_bps=10,
    )
    strings = _backtest(
        initial_quote_balance="10000",
        maker_fee_rate="0.004",
        taker_fee_rate="0.006",
        fixed_slippage_bps="5",
        spread_bps="10",
    )
    assert numbers == strings
    assert numbers.model_dump(mode="json") == strings.model_dump(mode="json")
    assert _execution_fingerprint(numbers.submission(_STRATEGY), "USDC") == (
        _execution_fingerprint(strings.submission(_STRATEGY), "USDC")
    )


def test_numbers_become_canonical_decimal_strings() -> None:
    """Exponents, trailing zeros, and integers render as plain decimal text."""
    assert json_number_as_decimal_text(5) == "5"
    assert json_number_as_decimal_text(5.0) == "5"
    assert json_number_as_decimal_text(0.0060) == "0.006"
    assert json_number_as_decimal_text(1e-05) == "0.00001"
    assert json_number_as_decimal_text("5.0") == "5.0"


@pytest.mark.parametrize("value", [True, float("nan"), float("inf")])
def test_booleans_and_non_finite_numbers_are_rejected(value: object) -> None:
    """Only finite numbers and strings are decimal inputs."""
    with pytest.raises(ValidationError):
        _backtest(
            initial_quote_balance="10000",
            maker_fee_rate="0.004",
            taker_fee_rate="0.006",
            fixed_slippage_bps=value,
        )


def _study(**fields: object) -> ResearchStudyStartRequest:
    """Return one WFO study start with the given decimal fields."""
    return ResearchStudyStartRequest.model_validate(
        {
            "kind": "walk_forward_optimization",
            "strategy_id": str(_STRATEGY_ID),
            "in_sample_bars": 48,
            "out_of_sample_bars": 24,
            "step_bars": 24,
            **fields,
        }
    )


def _bound(start: ResearchStudyStartRequest) -> ResearchStudyRequest:
    """Bind one start the way the server does once fingerprints and bounds are known."""
    payload = start.model_dump(
        mode="python",
        exclude={"strategy_id", "candidate_strategy_ids", "markets"},
    )
    payload |= {
        "strategy_fingerprint": _STRATEGY,
        "dataset_fingerprint": _DATASET,
        "evaluation_start": _START,
        "evaluation_end": _END,
    }
    return ResearchStudyRequest.model_validate(payload)


def test_numbers_and_strings_give_the_same_study_request_fingerprint() -> None:
    """Costs and axis values sent as numbers fingerprint exactly like strings."""
    numbers = _study(
        initial_quote_balance=10000,
        maker_fee_rate=0.004,
        taker_fee_rate=0.006,
        fixed_slippage_bps=5,
        parameter_axes=[{"indicator_id": "fast", "parameter": "period", "values": [10, 20]}],
    )
    strings = _study(
        initial_quote_balance="10000",
        maker_fee_rate="0.004",
        taker_fee_rate="0.006",
        fixed_slippage_bps="5",
        parameter_axes=[{"indicator_id": "fast", "parameter": "period", "values": ["10", "20"]}],
    )
    assert numbers == strings
    assert request_fingerprint(_bound(numbers)) == request_fingerprint(_bound(strings))


def test_oos_fraction_and_axis_values_accept_numbers() -> None:
    """``oos_fraction: 0.3`` and decimal axis values become their decimal strings."""
    start = _study(
        kind="oos_holdout",
        initial_quote_balance="10000",
        maker_fee_rate="0.004",
        taker_fee_rate="0.006",
        fixed_slippage_bps="5",
        oos_fraction=0.3,
        in_sample_bars=None,
        out_of_sample_bars=None,
        step_bars=None,
    )
    assert start.oos_fraction == "0.3"
    axis = ParameterAxis.model_validate(
        {"target": "exits", "parameter": "initial_stop_multiple", "values": [1.5, 2]}
    )
    assert axis.values == ("1.5", "2")


def test_portfolio_backtest_costs_accept_numbers() -> None:
    """The portfolio backtest request takes the same JSON-number costs."""
    numbers = PortfolioBacktestRequest.model_validate(
        {"maker_fee_rate": 0.004, "taker_fee_rate": 0.006, "fixed_slippage_bps": 5}
    )
    strings = PortfolioBacktestRequest.model_validate(
        {"maker_fee_rate": "0.004", "taker_fee_rate": "0.006", "fixed_slippage_bps": "5"}
    )
    assert numbers == strings
