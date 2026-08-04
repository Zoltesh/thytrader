"""Behavioral tests for the read-only immutable backtest result API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import UUID

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.backtest.benchmark import calculate_buy_and_hold_benchmark
from thytrader.backtest.kernel import simulate_backtest
from thytrader.backtest.models import BacktestBenchmark, BacktestResult, backtest_result_fingerprint
from thytrader.config import Settings
from thytrader.market_data.models import Candle
from thytrader.persistence.backtest_benchmarks import BacktestBenchmarkUnavailableError
from thytrader.persistence.backtest_results import (
    BacktestResultNotFoundError,
    BacktestResultSummaryView,
    BacktestResultUnavailableError,
)
from thytrader.research.models import (
    BarExecutionAssumptions,
    BrokerAssumptions,
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    ResearchRunSpecification,
    WarmupWindow,
)
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint

if TYPE_CHECKING:
    import pytest


def _strategy() -> StrategyDefinition:
    """Build the narrow published profile used by the simulation vector."""
    payload = cast(
        "dict[str, object]",
        json.loads(Path("tests/strategies/golden/reference_strategy_v1.json").read_text()),
    )
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
        "side": "long",
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
        "initial_stop": {"kind": "atr_multiple", "atr_indicator": "atr", "multiple": "2"},
        "take_profit": {"kind": "reward_risk", "multiple": "2"},
        "trailing_stop": {"enabled": False},
        "time_exit": {"max_bars_held": 96},
    }
    payload["sizing"] = {
        "kind": "risk_fraction",
        "risk_fraction": "0.01",
        "min_quote_notional": "1",
        "max_quote_notional": "1000",
    }
    payload["portfolio_limits"] = {
        "max_strategy_exposure_fraction": "1",
        "max_concurrent_positions": 1,
    }
    return StrategyDefinition.model_validate(payload)


def _run(strategy: StrategyDefinition) -> ResearchRunSpecification:
    """Build one executable run over the deterministic candle vector."""
    starts_at = datetime(2026, 8, 1, 2, tzinfo=UTC)
    return ResearchRunSpecification(
        schema_version="1.0",
        run_id=UUID("019cae99-3e00-7000-8000-000000000001"),
        created_at=datetime(2026, 3, 2, 12, 50, 4, 416000, tzinfo=UTC),
        strategy_fingerprint=strategy_fingerprint(strategy),
        dataset_fingerprint="sha256:" + "a" * 64,
        evaluation=EvaluationWindow(starts_at=starts_at, ends_at=starts_at + timedelta(hours=2)),
        warmup=WarmupWindow(bars=2, starts_at=starts_at - timedelta(hours=2)),
        capital=CapitalAssumptions(quote_currency="USD", initial_quote_balance="10000"),
        costs=CostAssumptions(
            maker_fee_rate="0.001", taker_fee_rate="0.002", fixed_slippage_bps="10"
        ),
        bar_execution=BarExecutionAssumptions(
            signal_timing="completed_candle_close", fill_timing="next_candle_open"
        ),
        engine_contract_version="thytrader-bar-backtest-v1",
        random_seed=0,
    )


def _candles() -> tuple[Candle, ...]:
    """Return warmup, one signal, one filled target, and one final fill candle."""
    start = datetime(2026, 8, 1, tzinfo=UTC)
    rows = (
        ("10", "11", "9", "10"),
        ("11", "12", "10", "11"),
        ("14", "15", "12", "14"),
        ("15", "30", "10", "10"),
        ("10", "11", "9", "10"),
    )
    return tuple(
        Candle(
            starts_at=start + timedelta(hours=index),
            open=Decimal(open_),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
            volume=Decimal("10"),
        )
        for index, (open_, high, low, close) in enumerate(rows)
    )


def _result() -> BacktestResult:
    """Build one deterministic result from the shared simulation vector."""
    strategy = _strategy()
    return simulate_backtest(_run(strategy), strategy, _candles())


def _v2_result() -> BacktestResult:
    """Build a V2 result carrying immutable broker and executable-fill evidence."""
    strategy = _strategy()
    legacy = _run(strategy)
    specification = ResearchRunSpecification.model_validate(
        {
            **legacy.model_dump(),
            "engine_contract_version": "thytrader-bar-backtest-v2",
            "broker": BrokerAssumptions(
                price_model="constant_spread_bps",
                spread_bps="10",
                fill_policy="full",
                trigger_evaluation="bid_side",
                equity_marking="bid_close",
            ).model_dump(mode="json"),
        }
    )
    return simulate_backtest(specification, strategy, _candles())


class InMemoryBacktestResultReader:
    """Deterministic read-only store used only by API behavior tests."""

    def __init__(self, results: tuple[BacktestResult, ...]) -> None:
        """Index supplied results by their content identity."""
        self._results = {backtest_result_fingerprint(result): result for result in results}

    async def list_summaries(
        self,
        *,
        run_fingerprint: str | None = None,
        strategy_fingerprint: str | None = None,
        dataset_fingerprint: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[BacktestResultSummaryView, ...]:
        """Return newest-first summary views honoring one optional filter."""
        del offset
        views = []
        for fingerprint, result in self._results.items():
            if run_fingerprint is not None and result.run_fingerprint != run_fingerprint:
                continue
            if (
                strategy_fingerprint is not None
                and result.strategy_fingerprint != strategy_fingerprint
            ):
                continue
            if (
                dataset_fingerprint is not None
                and result.dataset_fingerprint != dataset_fingerprint
            ):
                continue
            views.append(
                BacktestResultSummaryView(
                    result_fingerprint=fingerprint,
                    run_fingerprint=result.run_fingerprint,
                    strategy_fingerprint=result.strategy_fingerprint,
                    dataset_fingerprint=result.dataset_fingerprint,
                    engine_contract_version=result.engine_contract_version,
                    published_at=datetime(2026, 8, 2, tzinfo=UTC),
                    summary=result.summary,
                )
            )
        return tuple(views[:limit])

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Return one stored result or signal a miss."""
        try:
            return self._results[result_fingerprint]
        except KeyError:
            raise BacktestResultNotFoundError("missing") from None


class MismatchedBacktestResultReader(InMemoryBacktestResultReader):
    """Reader that deliberately returns a valid result for the wrong requested identity."""

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Return the stored result regardless of the requested fingerprint."""
        del result_fingerprint
        return next(iter(self._results.values()))


class ForgedBacktestResultReader(InMemoryBacktestResultReader):
    """Reader that returns a model-copy result whose canonical fields were bypassed."""

    def __init__(self, result: BacktestResult) -> None:
        """Keep one forged result without fingerprinting it at construction time."""
        self._forged_result = result

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Return the forged result regardless of the requested identity."""
        del result_fingerprint
        return self._forged_result


class InMemoryBacktestBenchmarkReader:
    """Deterministic read-only benchmark store used only by API behavior tests."""

    def __init__(self, benchmarks: tuple[BacktestBenchmark, ...]) -> None:
        """Index supplied derived comparisons by their result identity."""
        self._benchmarks = {benchmark.result_fingerprint: benchmark for benchmark in benchmarks}

    async def load(self, result_fingerprint: str) -> BacktestBenchmark:
        """Return one derived comparison or signal a miss."""
        try:
            return self._benchmarks[result_fingerprint]
        except KeyError:
            raise BacktestResultNotFoundError("missing") from None


class UnavailableBacktestBenchmarkReader:
    """Benchmark reader that always reports durable source storage as unreachable."""

    async def load(self, result_fingerprint: str) -> BacktestBenchmark:
        """Raise a redacted benchmark unavailability failure."""
        del result_fingerprint
        raise BacktestBenchmarkUnavailableError("unavailable")


class UnavailableBacktestResultReader:
    """Store that always reports durable storage as unreachable."""

    async def list_summaries(
        self,
        *,
        run_fingerprint: str | None = None,
        strategy_fingerprint: str | None = None,
        dataset_fingerprint: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[()]:
        """Raise a redacted unavailability failure."""
        del run_fingerprint, strategy_fingerprint, dataset_fingerprint, limit, offset
        raise BacktestResultUnavailableError("unavailable")

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Raise a redacted unavailability failure."""
        del result_fingerprint
        raise BacktestResultUnavailableError("unavailable")


def test_backtests_report_unavailable_when_persistence_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled durable storage must not be represented as empty results."""
    monkeypatch.setenv("THYTRADER_DATABASE_URL", "")
    app = create_app(Settings())

    with TestClient(app) as client:
        response = client.get("/api/v1/backtests")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "backtests_unavailable"


def test_backtests_list_returns_empty_page_when_none_exist() -> None:
    """A configured store with no results returns an empty bounded page."""
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=InMemoryBacktestResultReader(()),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/backtests")

    assert response.status_code == 200
    payload = response.json()
    assert payload["entries"] == []
    assert payload["returned"] == 0


def test_backtests_list_returns_summary_without_trade_ledger() -> None:
    """The discovery page carries metrics and identities, not the full ledger."""
    result = _result()
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=InMemoryBacktestResultReader((result,)),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/backtests")

    assert response.status_code == 200
    entry = response.json()["entries"][0]
    assert entry["result_fingerprint"] == backtest_result_fingerprint(result)
    assert entry["run_fingerprint"] == result.run_fingerprint
    assert entry["engine_contract_version"] == "thytrader-bar-backtest-v1"
    assert entry["published_at"].endswith("Z")
    assert entry["summary"]["trade_count"] == result.summary.trade_count
    assert "trades" not in entry
    assert "equity_curve" not in entry


def test_backtests_detail_serializes_v2_broker_and_fill_evidence() -> None:
    """V2 detail exposes canonical broker assumptions and decimal-string spread evidence."""
    result = _v2_result()
    fingerprint = backtest_result_fingerprint(result)
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=InMemoryBacktestResultReader((result,)),
    )

    with TestClient(app) as client:
        list_response = client.get("/api/v1/backtests")
        detail_response = client.get(f"/api/v1/backtests/{fingerprint}")

    assert list_response.status_code == 200
    entry = list_response.json()["entries"][0]
    assert entry["engine_contract_version"] == "thytrader-bar-backtest-v2"
    assert detail_response.status_code == 200
    payload = detail_response.json()["result"]
    assert payload["broker"]["spread_bps"] == "10"
    assert payload["broker"]["trigger_evaluation"] == "bid_side"
    assert payload["summary"]["total_spread_cost"] == result.summary.total_spread_cost
    assert payload["trades"][0]["entry"]["executable_side"] == "ask"
    assert payload["trades"][0]["exit"]["executable_side"] == "bid"


def test_backtests_detail_rejects_mismatched_reader_identity() -> None:
    """The detail endpoint must not label a reader result with a different requested identity."""
    result = _result()
    requested_fingerprint = "sha256:" + "f" * 64
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=MismatchedBacktestResultReader((result,)),
    )

    with TestClient(app) as client:
        response = client.get(f"/api/v1/backtests/{requested_fingerprint}")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "backtests_unavailable"


def test_backtests_detail_redacts_forged_result_revalidation_failure() -> None:
    """A forged stored result must not escape as an unhandled HTTP 500."""
    result = _result()
    forged = result.model_copy(
        update={"summary": result.summary.model_copy(update={"total_net_pnl": "NaN"})}
    )
    requested_fingerprint = "sha256:" + "f" * 64
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=ForgedBacktestResultReader(forged),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"/api/v1/backtests/{requested_fingerprint}")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "backtests_unavailable"
    assert "NaN" not in response.text


def test_backtests_benchmark_returns_derived_buy_and_hold_evidence() -> None:
    """The benchmark endpoint exposes a comparison without changing the immutable result payload."""
    strategy = _strategy()
    specification = _run(strategy)
    result = simulate_backtest(specification, strategy, _candles())
    benchmark = calculate_buy_and_hold_benchmark(result, specification, _candles())
    fingerprint = backtest_result_fingerprint(result)
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=InMemoryBacktestResultReader((result,)),
        backtest_benchmark_reader=InMemoryBacktestBenchmarkReader((benchmark,)),
    )

    with TestClient(app) as client:
        response = client.get(f"/api/v1/backtests/{fingerprint}/benchmark")

    assert response.status_code == 200
    payload = response.json()
    assert payload["result_fingerprint"] == fingerprint
    assert payload["benchmark"]["benchmark_contract_version"] == "thytrader-buy-and-hold-v1"
    assert payload["benchmark"]["total_fees"] == benchmark.total_fees


def test_backtests_benchmark_redacts_forged_model_copy() -> None:
    """A forged benchmark model must be revalidated before it reaches the HTTP response."""
    strategy = _strategy()
    specification = _run(strategy)
    result = simulate_backtest(specification, strategy, _candles())
    benchmark = calculate_buy_and_hold_benchmark(result, specification, _candles())
    forged = benchmark.model_copy(update={"entry_price": "NaN"})
    fingerprint = backtest_result_fingerprint(result)
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=InMemoryBacktestResultReader((result,)),
        backtest_benchmark_reader=InMemoryBacktestBenchmarkReader((forged,)),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"/api/v1/backtests/{fingerprint}/benchmark")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "backtests_unavailable"
    assert "NaN" not in response.text


def test_backtests_benchmark_redacts_shape_valid_forgery() -> None:
    """A shape-valid mutated benchmark must not pass its derived-evidence boundary."""
    strategy = _strategy()
    specification = _run(strategy)
    result = simulate_backtest(specification, strategy, _candles())
    benchmark = calculate_buy_and_hold_benchmark(result, specification, _candles())
    forged = benchmark.model_copy(update={"entry_price": "999"})
    fingerprint = backtest_result_fingerprint(result)
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=InMemoryBacktestResultReader((result,)),
        backtest_benchmark_reader=InMemoryBacktestBenchmarkReader((forged,)),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"/api/v1/backtests/{fingerprint}/benchmark")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "backtests_unavailable"
    assert '"entry_price":"999"' not in response.text


def test_backtests_benchmark_returns_404_for_unknown_result() -> None:
    """A benchmark request for an unknown result has the same redacted identity semantics."""
    app = create_app(
        Settings(_env_file=None),
        backtest_benchmark_reader=InMemoryBacktestBenchmarkReader(()),
    )

    with TestClient(app) as client:
        response = client.get(f"/api/v1/backtests/{'sha256:' + 'f' * 64}/benchmark")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "backtest_not_found"


def test_backtests_benchmark_failure_is_redacted() -> None:
    """Benchmark dependency failures must not leak source or storage details."""
    app = create_app(
        Settings(_env_file=None),
        backtest_benchmark_reader=UnavailableBacktestBenchmarkReader(),
    )

    with TestClient(app) as client:
        response = client.get(f"/api/v1/backtests/{'sha256:' + 'a' * 64}/benchmark")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "backtests_unavailable"
    assert "Traceback" not in response.text


def test_backtests_list_filters_by_strategy_fingerprint() -> None:
    """A matching source filter returns rows; a non-matching filter returns none."""
    result = _result()
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=InMemoryBacktestResultReader((result,)),
    )

    with TestClient(app) as client:
        matched = client.get(
            f"/api/v1/backtests?strategy_fingerprint={result.strategy_fingerprint}"
        )
        missed = client.get(f"/api/v1/backtests?strategy_fingerprint={'sha256:' + '0' * 64}")

    assert matched.json()["returned"] == 1
    assert missed.json()["entries"] == []


def test_backtests_list_rejects_multiple_filters() -> None:
    """Only one source fingerprint filter is accepted per discovery request."""
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=InMemoryBacktestResultReader(()),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/backtests"
            f"?run_fingerprint={'sha256:' + '0' * 64}"
            f"&strategy_fingerprint={'sha256:' + '1' * 64}"
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "backtest_invalid"


def test_backtests_list_rejects_malformed_fingerprint_filter() -> None:
    """Malformed fingerprint filters are rejected before any storage query."""
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=InMemoryBacktestResultReader(()),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/backtests?strategy_fingerprint=not-a-fingerprint")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "backtest_invalid"


def test_backtests_list_rejects_out_of_range_limit() -> None:
    """Pagination bounds are enforced by the request contract."""
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=InMemoryBacktestResultReader(()),
    )

    with TestClient(app) as client:
        too_large = client.get("/api/v1/backtests?limit=101")
        too_small = client.get("/api/v1/backtests?limit=0")

    assert too_large.status_code == 422
    assert too_small.status_code == 422


def test_backtests_detail_returns_full_reverified_result() -> None:
    """The detail endpoint returns the complete trade ledger and equity curve."""
    result = _result()
    fingerprint = backtest_result_fingerprint(result)
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=InMemoryBacktestResultReader((result,)),
    )

    with TestClient(app) as client:
        response = client.get(f"/api/v1/backtests/{fingerprint}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["result_fingerprint"] == fingerprint
    assert payload["result"]["summary"]["trade_count"] == result.summary.trade_count
    assert len(payload["result"]["trades"]) == len(result.trades)
    assert len(payload["result"]["equity_curve"]) == len(result.equity_curve)


def test_backtests_detail_returns_404_for_unknown_fingerprint() -> None:
    """A well-formed but unknown identity returns a redacted not-found."""
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=InMemoryBacktestResultReader(()),
    )

    with TestClient(app) as client:
        response = client.get(f"/api/v1/backtests/{'sha256:' + 'f' * 64}")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "backtest_not_found"


def test_backtests_detail_rejects_malformed_fingerprint() -> None:
    """A malformed path identity is rejected before any storage query."""
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=InMemoryBacktestResultReader(()),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/backtests/not-a-fingerprint")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "backtest_invalid"


def test_backtests_failure_is_redacted() -> None:
    """Storage failures must not leak internal detail to the browser."""
    app = create_app(
        Settings(_env_file=None),
        backtest_result_store=UnavailableBacktestResultReader(),
    )

    with TestClient(app) as client:
        list_response = client.get("/api/v1/backtests")
        detail_response = client.get(f"/api/v1/backtests/{'sha256:' + 'a' * 64}")

    assert list_response.status_code == 503
    assert detail_response.status_code == 503
    assert "unavailable" in list_response.json()["detail"]["message"].lower()
    assert "Traceback" not in list_response.text
