"""PostgreSQL integration: a multi-instrument document (ADR 0056) backtests end to end."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import os
from typing import TYPE_CHECKING

from pydantic import SecretStr
import pytest

from tests.portfolios.fixtures import DATA_START, write_dataset
from thytrader.backtest.submission import BacktestSubmissionRejectedError, BacktestSubmissionRequest
from thytrader.market_data.datasets import DatasetStore
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_backtest_submitter import PostgresBacktestSubmitter
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.strategies.models import StrategyDefinition

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine

_TEST_DATABASE_URL = os.environ.get("THYTRADER_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)

_BARS = 400


def _engine() -> AsyncEngine:
    """Open one engine against the configured integration database."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    return create_engine(SecretStr(_TEST_DATABASE_URL))


def _two_product_definition() -> StrategyDefinition:
    """A BTC-USDC 1h template that also trades ETH-USDC under the same rules."""
    template = create_template_strategy(product_id="BTC-USDC", timeframe="1h")
    return StrategyDefinition.model_validate(
        {
            **template.model_dump(mode="json"),
            "additional_instruments": [
                {"product_id": "ETH-USDC", "base_currency": "ETH", "quote_currency": "USDC"}
            ],
        }
    )


def _request(
    strategy_fingerprint: str, btc: str, extra: tuple[str, str]
) -> BacktestSubmissionRequest:
    """One dated submission binding the primary and one additional-instrument dataset."""
    return BacktestSubmissionRequest.model_validate(
        {
            "strategy_fingerprint": strategy_fingerprint,
            "dataset_fingerprint": btc,
            "additional_instrument_datasets": [
                {"product_id": extra[0], "dataset_fingerprint": extra[1]}
            ],
            "evaluation_start": DATA_START + timedelta(hours=150),
            "evaluation_end": DATA_START + timedelta(hours=_BARS - 2),
            "initial_quote_balance": "1000",
            "maker_fee_rate": "0.005",
            "taker_fee_rate": "0.009",
            "fixed_slippage_bps": "5",
        }
    )


def test_multi_instrument_backtest_publishes_one_result(tmp_path: Path) -> None:
    """Every covered product's dataset binds, the run publishes, and the result reloads."""
    btc = write_dataset(tmp_path, "BTC-USDC", "1h", start=DATA_START, count=_BARS)
    eth = write_dataset(tmp_path, "ETH-USDC", "1h", start=DATA_START, count=_BARS, base=200)
    dataset_store = DatasetStore(tmp_path)

    async def exercise() -> None:
        engine = _engine()
        strategies = PostgresStrategyStore(engine)
        record = await create_strategy_from_definition(strategies, _two_product_definition())
        try:
            snapshot = await strategies.snapshot(record.strategy_id)
            submitter = PostgresBacktestSubmitter(engine, dataset_store)
            published = await submitter.submit(
                _request(snapshot.strategy_fingerprint, btc, ("ETH-USDC", eth))
            )
            results = PostgresBacktestResultStore(
                engine,
                research_run_store=PostgresResearchRunStore(engine),
                dataset_store=dataset_store,
            )
            result = await results.load(published.result_fingerprint)
            assert result.summary.initial_equity == "1000"
            again = await submitter.submit(
                _request(snapshot.strategy_fingerprint, btc, ("ETH-USDC", eth))
            )
            assert again == published
        finally:
            await strategies.delete(record.strategy_id)
            await dispose(engine)

    asyncio.run(exercise())


def test_dataset_for_an_uncovered_product_is_a_caller_rejection(tmp_path: Path) -> None:
    """Binding a product the document does not cover names the mismatch instead of a 503."""
    btc = write_dataset(tmp_path, "BTC-USDC", "1h", start=DATA_START, count=_BARS)
    sol = write_dataset(tmp_path, "SOL-USDC", "1h", start=DATA_START, count=_BARS, base=50)
    dataset_store = DatasetStore(tmp_path)

    async def exercise() -> None:
        engine = _engine()
        strategies = PostgresStrategyStore(engine)
        definition = create_template_strategy(product_id="BTC-USDC", timeframe="1h")
        record = await create_strategy_from_definition(strategies, definition)
        try:
            snapshot = await strategies.snapshot(record.strategy_id)
            submitter = PostgresBacktestSubmitter(engine, dataset_store)
            request = BacktestSubmissionRequest.model_validate(
                {
                    **_request(snapshot.strategy_fingerprint, btc, ("ETH-USDC", btc)).model_dump(
                        mode="json", exclude={"additional_instrument_datasets"}
                    ),
                    "dataset_fingerprint": sol,
                }
            )
            with pytest.raises(BacktestSubmissionRejectedError, match="SOL-USDC 1h"):
                await submitter.submit(request)
        finally:
            await strategies.delete(record.strategy_id)
            await dispose(engine)

    asyncio.run(exercise())
