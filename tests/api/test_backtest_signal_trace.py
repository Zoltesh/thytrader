"""HTTP signal trace for a result's run and stored diagnostics on result detail (ADR 0090)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from tests.api.test_backtests import (
    CostProjectingBacktestResultReader,
    InMemoryBacktestResultReader,
    _candles,
    _run,
    _strategy,
)
from tests.execution.decision_support import Catalog
from thytrader.api.app import create_app
from thytrader.backtest.kernel import simulate_backtest_with_diagnostics
from thytrader.backtest.models import (
    BacktestDiagnostics,
    BacktestResult,
    backtest_result_fingerprint,
)
from thytrader.config import Settings
from thytrader.market_data.datasets import DatasetStore

if TYPE_CHECKING:
    from pathlib import Path

    from thytrader.evaluation.models import ResearchRunSpecification
    from thytrader.market_data.models import Candle


class _FixedCandles(DatasetStore):
    """Dataset store double that serves one candle vector for every fingerprint."""

    def __init__(self, root: Path, candles: tuple[Candle, ...]) -> None:
        """Bind the candles every load returns."""
        super().__init__(root)
        self._candles = candles

    def load_candles(self, content_fingerprint: str) -> tuple[Candle, ...]:
        """Return the bound candles regardless of identity (tests only)."""
        del content_fingerprint
        return self._candles


class _DiagnosticsReader(CostProjectingBacktestResultReader):
    """Result reader that also stores diagnostics beside each result."""

    def __init__(
        self,
        results: tuple[BacktestResult, ...],
        specification: ResearchRunSpecification,
        diagnostics: BacktestDiagnostics,
    ) -> None:
        """Index results and keep one diagnostics record."""
        super().__init__(results, specification)
        self._diagnostics = diagnostics

    async def load_diagnostics(self, result_fingerprint: str) -> BacktestDiagnostics | None:
        """Return the stored diagnostics for known results."""
        del result_fingerprint
        return self._diagnostics


def _client(tmp_path: Path, candles: tuple[Candle, ...] | None = None) -> tuple[TestClient, str]:
    """App with one simulated result, its source run, its snapshot, and its candles."""
    strategy = _strategy()
    specification = _run(strategy)
    result, diagnostics = simulate_backtest_with_diagnostics(specification, strategy, _candles())
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=_DiagnosticsReader((result,), specification, diagnostics),
        strategy_snapshot_store=Catalog(strategy),
        dataset_store=_FixedCandles(tmp_path, candles or _candles()),
    )
    return TestClient(app), backtest_result_fingerprint(result)


def test_signal_trace_reproduces_the_results_trace_identity(tmp_path: Path) -> None:
    """The API re-evaluates the run and returns a page bound to the result's trace."""
    client, fingerprint = _client(tmp_path)
    with client:
        response = client.get(f"/api/v1/backtests/{fingerprint}/signal-trace")
        full = client.get(f"/api/v1/backtests/{fingerprint}?detail=full").json()
    assert response.status_code == 200
    page = response.json()
    assert page["result_fingerprint"] == fingerprint
    assert page["signal_trace_fingerprint"] == full["result"]["signal_trace_fingerprint"]
    assert page["product_id"] == _strategy().instrument.product_id
    assert page["total_records"] == len(page["records"]) == 2
    assert page["counts"] == {"matched": 1, "not_matched": 1, "undefined": 0}
    assert page["next_cursor"] is None
    assert {record["entry_condition"] for record in page["records"]} == {
        "matched",
        "not_matched",
    }


def test_signal_trace_filters_by_outcome_and_pages(tmp_path: Path) -> None:
    """``outcome`` narrows the records; ``limit`` and ``next_cursor`` bound the page."""
    client, fingerprint = _client(tmp_path)
    with client:
        matched = client.get(f"/api/v1/backtests/{fingerprint}/signal-trace?outcome=matched").json()
        first = client.get(f"/api/v1/backtests/{fingerprint}/signal-trace?limit=1").json()
        second = client.get(
            f"/api/v1/backtests/{fingerprint}/signal-trace?limit=1&cursor={first['next_cursor']}"
        ).json()
    assert [record["entry_condition"] for record in matched["records"]] == ["matched"]
    assert matched["counts"]["matched"] == 1
    assert first["returned"] == 1
    assert first["next_cursor"] is not None
    assert second["offset"] == 1
    assert second["next_cursor"] is None
    assert first["records"][0] != second["records"][0]


def test_signal_trace_fails_closed_when_the_trace_does_not_match_the_result(
    tmp_path: Path,
) -> None:
    """Different candles than the result used cannot masquerade as its trace."""
    altered = tuple(
        replace(candle, close=candle.close + 1) if index == 2 else candle
        for index, candle in enumerate(_candles())
    )
    client, fingerprint = _client(tmp_path, altered)
    with client:
        response = client.get(f"/api/v1/backtests/{fingerprint}/signal-trace")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "signal_trace_unavailable"


def test_signal_trace_reports_unknown_results_and_missing_run_stores(tmp_path: Path) -> None:
    """Unknown fingerprints are 404; a store without published runs is a 503."""
    client, _fingerprint = _client(tmp_path)
    with client:
        missing = client.get("/api/v1/backtests/sha256:" + "f" * 64 + "/signal-trace")
        malformed = client.get("/api/v1/backtests/not-a-fingerprint/signal-trace")
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
            f"/api/v1/backtests/{backtest_result_fingerprint(result)}/signal-trace"
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "signal_trace_unavailable"


def test_result_detail_carries_stored_diagnostics(tmp_path: Path) -> None:
    """Summary and full detail expose the entry funnel without touching result bytes."""
    client, fingerprint = _client(tmp_path)
    with client:
        summary = client.get(f"/api/v1/backtests/{fingerprint}").json()
        full = client.get(f"/api/v1/backtests/{fingerprint}?detail=full").json()
    for body in (summary, full):
        diagnostics = body["diagnostics"]
        assert diagnostics["diagnostics_version"] == "thytrader-backtest-diagnostics-v1"
        assert diagnostics["signals_matched"] == 1
        assert diagnostics["entries_filled"] == 1
    assert "diagnostics" not in full["result"]
