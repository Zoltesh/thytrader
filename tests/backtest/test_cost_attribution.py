"""Exact fee totals retain canonical evidence and disclose accounting differences."""

from decimal import ROUND_UP, Decimal, Inexact, localcontext
import json
from typing import Literal

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from tests.api.test_backtests import InMemoryBacktestResultReader, _result, _spread_result
from tests.backtest.test_kernel import _candles, _run, _strategy
from thytrader.api.app import create_app
from thytrader.backtest.cost_attribution import BacktestCostAttribution, compute_cost_attribution
from thytrader.backtest.kernel import simulate_backtest
from thytrader.backtest.models import backtest_result_fingerprint, canonical_backtest_result_bytes
from thytrader.config import Settings
from thytrader.research.http import show_result
from thytrader.research.stress import ExecutionStress
from thytrader.strategies.models import StrategyDefinition


@pytest.mark.parametrize("spread", [False, True])
def test_attribution_uses_recorded_fills_without_charging_spread_twice(spread: bool) -> None:
    """Actual simulator outputs use ledger gross PnL and actual entry/exit fees."""
    result = _spread_result() if spread else _result()
    canonical = canonical_backtest_result_bytes(result)
    attribution = compute_cost_attribution(result)
    with localcontext() as context:
        context.prec = 12500
        gross = sum((Decimal(trade.gross_pnl) for trade in result.trades), start=Decimal(0))
        entry = sum((Decimal(trade.entry.fee) for trade in result.trades), start=Decimal(0))
        exit_fees = sum((Decimal(trade.exit.fee) for trade in result.trades), start=Decimal(0))
        net = sum((Decimal(trade.net_pnl) for trade in result.trades), start=Decimal(0))
        assert Decimal(attribution.fill_price_pnl_before_fees) == gross
        assert Decimal(attribution.entry_fees) == entry
        assert Decimal(attribution.exit_fees) == exit_fees
        assert Decimal(attribution.net_pnl) == net
        assert Decimal(attribution.accounting_residual) == net - gross + entry + exit_fees
        assert (
            Decimal(attribution.summary_net_pnl_delta)
            == Decimal(result.summary.total_net_pnl) - net
        )
    assert attribution.trade_count == len(result.trades)
    assert attribution.result_fingerprint == backtest_result_fingerprint(result)
    assert canonical_backtest_result_bytes(result) == canonical


def test_aggregation_preserves_more_than_64_digits_under_hostile_context() -> None:
    """Summing valid source amounts must not round at either engine or caller precision."""
    result = _result()
    trade = result.trades[0].model_copy(update={"gross_pnl": "9" * 64})
    result = result.model_copy(update={"trades": (trade, trade)})
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        attribution = compute_cost_attribution(result)
    assert attribution.fill_price_pnl_before_fees == "1" + "9" * 63 + "8"
    assert compute_cost_attribution(result) == attribution


def test_zero_trades_means_known_zero_fees_and_retains_summary_difference() -> None:
    """An empty ledger is known zero, while unrelated summary amounts remain visible."""
    result = _result().model_copy(update={"trades": ()})
    attribution = compute_cost_attribution(result)
    assert attribution.trade_count == 0
    for value in (
        attribution.fill_price_pnl_before_fees,
        attribution.entry_fees,
        attribution.exit_fees,
        attribution.net_pnl,
        attribution.accounting_residual,
    ):
        assert value == "0"
    assert attribution.summary_net_pnl_delta == result.summary.total_net_pnl


def test_attribution_rejects_altered_evidence_and_negative_fees() -> None:
    """Stored derived evidence verifies its own digest and refuses impossible charges."""
    payload = compute_cost_attribution(_result()).model_dump(mode="python")
    payload["entry_fees"] = "1"
    with pytest.raises(ValidationError, match="fingerprint"):
        BacktestCostAttribution.model_validate(payload)
    payload["entry_fees"] = "-1"
    with pytest.raises(ValidationError, match="negative"):
        BacktestCostAttribution.model_validate(payload)


def test_full_and_fallback_summary_api_compute_matching_attribution() -> None:
    """Full artifact reads and stores without bounded projections expose the same evidence."""
    result = _spread_result()
    app = create_app(Settings(), backtest_result_store=InMemoryBacktestResultReader((result,)))
    fingerprint = backtest_result_fingerprint(result)
    with TestClient(app) as client:
        summary = client.get(f"/api/v1/backtests/{fingerprint}")
        full = client.get(f"/api/v1/backtests/{fingerprint}?detail=full")
    assert summary.status_code == full.status_code == 200
    expected = compute_cost_attribution(result).model_dump(mode="json")
    assert summary.json()["cost_attribution"] == full.json()["cost_attribution"] == expected
    assert "cost_attribution" not in full.json()["result"]


@pytest.mark.parametrize("amount", ["1e1000000000", "NaN", "Infinity", "1.00", "-0", "invalid"])
def test_attribution_rejects_noncanonical_amounts_before_rendering(amount: str) -> None:
    """Untrusted metadata cannot trigger unbounded exponent rendering or conceal invalid money."""
    payload = compute_cost_attribution(_result()).model_dump(
        mode="python", exclude={"attribution_fingerprint"}
    )
    payload["entry_fees"] = amount
    with pytest.raises(ValidationError):
        BacktestCostAttribution.model_validate(payload)


def test_research_cli_forwards_attribution_without_requesting_full_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """show-result retains exact string amounts and stays on detail=summary."""
    result = _result()
    expected = compute_cost_attribution(result).model_dump(mode="json")

    def request(*, method: str, url: str) -> dict[str, object]:
        """Provide only the small public response at the HTTP boundary."""
        assert method == "GET" and url.endswith("?detail=summary")
        return {"result_fingerprint": expected["result_fingerprint"], "cost_attribution": expected}

    monkeypatch.setattr("thytrader.research.http.request_json", request)
    payload = json.loads(show_result("http://localhost:8200", backtest_result_fingerprint(result)))
    assert payload["cost_attribution"] == expected


@pytest.mark.parametrize("side", ["long", "short"])
def test_partial_fill_attribution_uses_filled_quantity(side: Literal["long", "short"]) -> None:
    """Synthetic shorts and partial entries retain the simulator's recorded fee semantics."""
    strategy = _strategy()
    strategy = StrategyDefinition.model_validate(
        strategy.model_copy(
            update={"entry": strategy.entry.model_copy(update={"side": side})}
        ).model_dump(mode="python")
    )
    run = _run(strategy, spread_bps="25")
    run = run.model_copy(
        update={
            "costs": run.costs.model_copy(
                update={"execution_stress": ExecutionStress(entry_fill_fraction="0.5")}
            )
        }
    )
    result = simulate_backtest(run, strategy, _candles())
    assert result.trades
    attribution = compute_cost_attribution(result)
    assert attribution.entry_fees == result.trades[0].entry.fee
    assert attribution.exit_fees == result.trades[0].exit.fee
