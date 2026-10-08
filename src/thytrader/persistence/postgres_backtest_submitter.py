"""PostgreSQL wiring for the backtest submitter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.backtest.submission import StoreBacktestSubmitter
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.market_data.datasets import DatasetStore


class PostgresBacktestSubmitter(StoreBacktestSubmitter):
    """Submit backtests against the PostgreSQL strategy, run, and result stores."""

    def __init__(self, engine: AsyncEngine, dataset_store: DatasetStore) -> None:
        """Use one application-managed engine and immutable dataset root."""
        run_store = PostgresResearchRunStore(engine)
        super().__init__(
            strategy_store=PostgresStrategyStore(engine),
            run_store=run_store,
            result_store=PostgresBacktestResultStore(
                engine,
                research_run_store=run_store,
                dataset_store=dataset_store,
            ),
            dataset_store=dataset_store,
        )
