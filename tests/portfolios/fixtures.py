"""Shared portfolio test doubles: verified datasets, strategies, and child backtest fakes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.backtest.models import BacktestResult, BacktestSummary, EquityPoint
from thytrader.backtest.submission import BacktestSubmissionRequest, BacktestSubmissionResult
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import Candle, parse_candle_interval
from thytrader.market_data.quality import analyze_range
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from thytrader.persistence.backtest_results import BacktestResultSummaryView
    from thytrader.strategies.library import StrategyRecord
    from thytrader.strategies.memory_store import InMemoryStrategyStore

DATA_START = datetime(2026, 7, 1, tzinfo=UTC)


def write_dataset(
    root: Path, product_id: str, timeframe: str, *, start: datetime, count: int, base: int = 100
) -> str:
    """Publish one verified complete Coinbase dataset with a gentle sawtooth price."""
    step = parse_candle_interval(timeframe).duration
    opens = [Decimal(base + index % 9) for index in range(count)]
    closes = [Decimal(base + (index + 1) % 9) for index in range(count)]
    candles = tuple(
        Candle(
            starts_at=start + step * index,
            open=opens[index],
            high=max(opens[index], closes[index]) + 3,
            low=min(opens[index], closes[index]) - 3,
            close=closes[index],
            volume=Decimal(10),
        )
        for index in range(count)
    )
    end = start + step * count
    report = analyze_range(candles, parse_candle_interval(timeframe), start, end, now=end)
    return DatasetStore(root).write("coinbase", product_id, report).content_fingerprint


def strategy(
    store: InMemoryStrategyStore,
    product_id: str,
    timeframe: str = "1h",
    template: str = "ema-trend",
) -> StrategyRecord:
    """Create one valid template strategy."""
    definition = create_template_strategy(
        product_id=product_id, timeframe=timeframe, template=template
    )
    return asyncio.run(create_strategy_from_definition(store, definition))


def flat_result(request: BacktestSubmissionRequest, timeframe: str) -> BacktestResult:
    """A child result that holds its capital in cash for the whole dated window."""
    start = request.evaluation_start
    end = request.evaluation_end
    if start is None or end is None:
        raise AssertionError("child submissions are dated by the planner")
    step = parse_candle_interval(timeframe).duration
    capital = request.initial_quote_balance
    bars = int((end - start) / step)
    points = tuple(
        EquityPoint(
            candle_starts_at=start + step * index,
            cash=capital,
            base_quantity="0",
            mark_price="100",
            equity=capital,
        )
        for index in range(bars + 1)
    )
    return BacktestResult(
        schema_version="1.0",
        run_fingerprint="sha256:" + "5" * 64,
        strategy_fingerprint=request.strategy_fingerprint,
        dataset_fingerprint=request.dataset_fingerprint,
        signal_trace_fingerprint="sha256:" + "6" * 64,
        trades=(),
        equity_curve=points,
        summary=BacktestSummary(
            initial_equity=capital,
            final_equity=capital,
            total_net_pnl="0",
            total_return_fraction="0",
            gross_profit="0",
            gross_loss="0",
            win_rate="0",
            trade_count=0,
            winning_trade_count=0,
            maximum_drawdown="0",
            maximum_drawdown_fraction="0",
            exposure_bars=0,
            evaluation_bars=max(1, bars),
        ),
    )


class FakeChildBacktests:
    """A submitter plus result reader that returns flat child results for each request."""

    def __init__(self, timeframes: dict[str, str]) -> None:
        """Map strategy fingerprints to their decision timeframes."""
        self.timeframes = timeframes
        self.requests: list[BacktestSubmissionRequest] = []
        self.results: dict[str, BacktestResult] = {}

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Record the child request and keep a flat result for it."""
        self.requests.append(request)
        fingerprint = "sha256:" + f"{len(self.requests):064x}"
        self.results[fingerprint] = flat_result(
            request, self.timeframes[request.strategy_fingerprint]
        )
        return BacktestSubmissionResult(
            run_fingerprint="sha256:" + "7" * 64, result_fingerprint=fingerprint
        )

    async def list_summaries(
        self,
        *,
        run_fingerprint: str | None = None,
        strategy_fingerprint: str | None = None,
        dataset_fingerprint: str | None = None,
        strategy_id: UUID | None = None,
        limit: int,
        offset: int,
    ) -> tuple[BacktestResultSummaryView, ...]:
        """No listing is needed by portfolio backtests."""
        del run_fingerprint, strategy_fingerprint, dataset_fingerprint, strategy_id, limit, offset
        return ()

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Return the stored flat child result."""
        return self.results[result_fingerprint]


def datasets_for_two_sleeves(root: Path) -> tuple[str, str]:
    """BTC-USDC 1h for 400 hours from July 1; ETH-USDC 4h for 120 bars from July 2."""
    btc = write_dataset(root, "BTC-USDC", "1h", start=DATA_START, count=400)
    eth = write_dataset(
        root, "ETH-USDC", "4h", start=DATA_START + timedelta(days=1), count=120, base=50
    )
    return btc, eth
