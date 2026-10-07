"""HTTP per-bar explanations for a published backtest result (ADR 0116)."""

from dataclasses import replace
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from tests.api.test_backtest_signal_trace import _client, _FixedCandles
from tests.api.test_backtests import (
    InMemoryBacktestResultReader,
    _candles,
    _run,
    _strategy,
)
from tests.execution.decision_support import Catalog
from thytrader.api.app import create_app
from thytrader.backtest.kernel import simulate_backtest_with_diagnostics
from thytrader.backtest.models import backtest_result_fingerprint
from thytrader.config import Settings

if TYPE_CHECKING:
    from pathlib import Path


def test_bar_explanations_join_the_verified_trace_to_recorded_fills(tmp_path: Path) -> None:
    """One page explains entry outcomes and the result's own fills without lookahead."""
    client, fingerprint = _client(tmp_path)
    with client:
        response = client.get(f"/api/v1/backtests/{fingerprint}/bar-explanations")
        full = client.get(f"/api/v1/backtests/{fingerprint}?detail=full").json()
    assert response.status_code == 200
    page = response.json()
    assert page["schema_version"] == "thytrader-backtest-bar-explanation-v1"
    assert page["result_fingerprint"] == fingerprint
    assert page["run_fingerprint"] == full["result"]["run_fingerprint"]
    assert page["dataset_fingerprint"] == full["result"]["dataset_fingerprint"]
    assert page["signal_trace_fingerprint"] == full["result"]["signal_trace_fingerprint"]
    assert page["returned"] == page["total_bars"] == 2
    assert page["next_cursor"] is None
    entries = [record for record in page["records"] if record["entries"]]
    joined_exits = [record for record in page["records"] if record["exits"]]
    outside_exits = [item for item in page["outside_trace"] if item["kind"] == "exit"]
    assert len(entries) == 1
    disclosed_exits = joined_exits or outside_exits
    assert disclosed_exits
    exit_payload = disclosed_exits[0]["exits"][0] if joined_exits else disclosed_exits[0]["fill"]
    assert exit_payload["net_pnl"] == full["result"]["trades"][0]["net_pnl"]
    assert exit_payload["reason"] in {
        "stop_loss",
        "take_profit",
        "time_exit",
        "signal",
        "evaluation_end",
    }
    assert all(record["indicator_values"] for record in page["records"])


def test_bar_explanations_page_oldest_first(tmp_path: Path) -> None:
    """Limit and cursor bound the page without changing the underlying result."""
    client, fingerprint = _client(tmp_path)
    with client:
        first = client.get(f"/api/v1/backtests/{fingerprint}/bar-explanations?limit=1").json()
        second = client.get(
            f"/api/v1/backtests/{fingerprint}/bar-explanations?limit=1&cursor={first['next_cursor']}"
        ).json()
    assert first["returned"] == 1
    assert first["offset"] == 0
    assert second["offset"] == 1
    assert second["next_cursor"] is None
    assert first["records"][0]["candle_starts_at"] < second["records"][0]["candle_starts_at"]


def test_bar_explanations_fail_closed_on_mismatch_and_missing_runs(tmp_path: Path) -> None:
    """A trace that does not match the result, or a store without runs, is unavailable."""
    altered = tuple(
        replace(candle, close=candle.close + 1) if index == 2 else candle
        for index, candle in enumerate(_candles())
    )
    client, fingerprint = _client(tmp_path, altered)
    with client:
        mismatched = client.get(f"/api/v1/backtests/{fingerprint}/bar-explanations")
        missing = client.get("/api/v1/backtests/sha256:" + "e" * 64 + "/bar-explanations")
        malformed = client.get("/api/v1/backtests/not-a-fingerprint/bar-explanations")
    assert mismatched.status_code == 503
    assert mismatched.json()["detail"]["code"] == "bar_explanations_unavailable"
    assert missing.status_code == 404
    assert malformed.status_code == 400
    strategy = _strategy()
    result, _diagnostics = simulate_backtest_with_diagnostics(_run(strategy), strategy, _candles())
    bare = create_app(
        Settings(_env_file=None),
        backtest_result_store=InMemoryBacktestResultReader((result,)),
        strategy_snapshot_store=Catalog(strategy),
        dataset_store=_FixedCandles(tmp_path, _candles()),
    )
    with TestClient(bare) as client_without_runs:
        response = client_without_runs.get(
            f"/api/v1/backtests/{backtest_result_fingerprint(result)}/bar-explanations"
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "bar_explanations_unavailable"
