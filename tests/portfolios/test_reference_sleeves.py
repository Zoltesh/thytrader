"""Portfolio sleeves whose strategies read reference instruments (ADR 0096).

A sleeve book starts exactly like a single bot: its reference series must be on the
enabled market-data watchlist. Sleeves without references are unaffected.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from tests.portfolios.runtime_support import World, operator, portfolio, world
from thytrader.execution.service import ReferenceWatchlist
from thytrader.market_data.models import CandleInterval
from thytrader.market_data.watchlist import InMemoryMarketDataWatchlistStore, MarketDataWatchTarget
from thytrader.portfolios.models import PortfolioAggregate, SleeveAddRequest
from thytrader.portfolios.planning import _latest_bindings, _MissingDatasetsError
from thytrader.portfolios.runtime import PortfolioRuntimeService
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition

pytestmark = pytest.mark.anyio


def _with_watchlist(state: World, watchlist: InMemoryMarketDataWatchlistStore) -> World:
    """The same world whose runtime checks reference watches against ``watchlist``."""
    runtime = PortfolioRuntimeService(
        portfolios=state.portfolios,
        execution=state.execution,
        strategies=state.strategies,
        publication=state.strategies,
        risk_store=state.risk,
        live_allowed=True,
        audit=state.audit,
        reference_watches=ReferenceWatchlist(store=watchlist, provider="demo"),
    )
    return replace(state, runtime=runtime)


async def _mixed_portfolio(state: World) -> PortfolioAggregate:
    """A BTC EMA-trend sleeve plus an ETH sleeve gated on the BTC-USDC 1d reference."""
    current, _records = await portfolio(state, sleeves=(("BTC-USDC", "0.4"),))
    gated = create_template_strategy(
        product_id="ETH-USDC", timeframe="1h", template="btc-regime-gate"
    )
    record = await create_strategy_from_definition(state.strategies, gated)
    return await state.portfolios.add_sleeve(
        current.portfolio.portfolio_id,
        SleeveAddRequest(
            revision=current.portfolio.revision,
            strategy_id=record.strategy_id,
            weight_fraction="0.4",
        ),
        context=operator(),
    )


async def _watch_btc_daily(watchlist: InMemoryMarketDataWatchlistStore) -> None:
    """Put BTC-USDC 1d on the enabled watchlist."""
    await watchlist.upsert(
        MarketDataWatchTarget(
            provider="demo",
            product_id="BTC-USDC",
            timeframe=CandleInterval.ONE_DAY,
            lookback_hours=2424,
            enabled=True,
            updated_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
    )


async def test_unwatched_reference_fails_only_its_sleeve() -> None:
    """The plain sleeve starts; the reference sleeve reports the watch-add command."""
    watchlist = InMemoryMarketDataWatchlistStore()
    state = _with_watchlist(world(), watchlist)
    current = await _mixed_portfolio(state)
    result = await state.runtime.start(
        current.portfolio.portfolio_id, revision=current.portfolio.revision, context=operator()
    )
    outcomes = {item.outcome for item in result.outcomes}
    assert outcomes == {"started", "failed"}
    failed = next(item for item in result.outcomes if item.outcome == "failed")
    assert "thytrader-data watch-add --product-id BTC-USDC --timeframe 1d" in (failed.message or "")


def test_portfolio_backtest_binds_a_sleeves_reference_from_the_latest_datasets() -> None:
    """Sleeve backtests bind the reference series like every other clock (or name it)."""
    definition = create_template_strategy(
        product_id="ETH-USDC", timeframe="1h", template="btc-regime-gate"
    )
    eth = "sha256:" + "1" * 64
    btc = "sha256:" + "2" * 64
    bindings = _latest_bindings(definition, {("ETH-USDC", "1h"): eth, ("BTC-USDC", "1d"): btc})
    (reference,) = bindings.reference_dataset_fingerprints
    assert (reference.reference_id, reference.dataset_fingerprint) == ("btc", btc)
    with pytest.raises(_MissingDatasetsError, match="BTC-USDC 1d \\(reference instrument btc\\)"):
        _latest_bindings(definition, {("ETH-USDC", "1h"): eth})


async def test_watched_reference_sleeve_starts_like_any_other() -> None:
    """With BTC-USDC 1d watched, both sleeves start."""
    watchlist = InMemoryMarketDataWatchlistStore()
    await _watch_btc_daily(watchlist)
    state = _with_watchlist(world(), watchlist)
    current = await _mixed_portfolio(state)
    result = await state.runtime.start(
        current.portfolio.portfolio_id, revision=current.portfolio.revision, context=operator()
    )
    assert [item.outcome for item in result.outcomes] == ["started", "started"]
    books = await state.tagged(current.portfolio.portfolio_id)
    assert sorted(book.product_id for book in books) == ["BTC-USDC", "ETH-USDC"]
