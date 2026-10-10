"""PostgreSQL integration: a perp backtest binds recorded facts end to end (ADR 0128, P1-3)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import SecretStr
import pytest

from tests.portfolios.fixtures import DATA_START, write_dataset
from thytrader.backtest.submission import BacktestSubmissionRejectedError, BacktestSubmissionRequest
from thytrader.exchanges.coinbase_futures_catalog import parse_futures_row
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.futures_observations import (
    FundingSample,
    FuturesInstrumentObservation,
    instrument_observation,
)
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_backtest_submitter import PostgresBacktestSubmitter
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_futures import PostgresFuturesObservationStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.strategies.models import StrategyDefinition

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncEngine

_TEST_DATABASE_URL = os.environ.get("THYTRADER_TEST_DATABASE_URL")
_LISTING = Path("tests/exchanges/fixtures/coinbase_futures_listing.json")
_BARS = 400
_STARTS = DATA_START + timedelta(hours=150)
_ENDS = DATA_START + timedelta(hours=_BARS - 2)

pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


def _engine() -> AsyncEngine:
    """Open one engine against the configured integration database."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    return create_engine(SecretStr(_TEST_DATABASE_URL))


def _observation(product_id: str) -> FuturesInstrumentObservation:
    """The BIP perp fixture's recorded facts under a unique id."""
    payload: dict[str, Any] = json.loads(_LISTING.read_text())
    row = next(item for item in payload["products"] if item["product_id"] == "BIP-20DEC30-CDE")
    product = parse_futures_row(row)
    assert product is not None
    return replace(instrument_observation(product), product_id=product_id)


def _definition(product_id: str) -> StrategyDefinition:
    """The 1h EMA-trend template re-targeted at one perp."""
    template = create_template_strategy(product_id="BTC-USD", timeframe="1h")
    return StrategyDefinition.model_validate(
        {
            **template.model_dump(mode="json"),
            "instrument": {
                "product_id": product_id,
                "base_currency": "BTC",
                "quote_currency": "USD",
                "kind": "future",
            },
            "derivatives": {"max_leverage": "2"},
        }
    )


def _request(strategy_fingerprint: str, dataset: str) -> BacktestSubmissionRequest:
    """One dated perp submission with a per-contract fee."""
    return BacktestSubmissionRequest.model_validate(
        {
            "strategy_fingerprint": strategy_fingerprint,
            "dataset_fingerprint": dataset,
            "evaluation_start": _STARTS,
            "evaluation_end": _ENDS,
            "initial_quote_balance": "10000",
            "maker_fee_rate": "0",
            "taker_fee_rate": "0.0005",
            "fixed_slippage_bps": "2",
            "futures": {"fee_per_contract": "0.15"},
        }
    )


async def _record_funding(
    store: PostgresFuturesObservationStore, product_id: str, last: datetime
) -> None:
    """Poll one sample per hour through ``last`` plus one more, so every hour settles."""
    hour = _STARTS
    first = True
    while hour <= last + timedelta(hours=1):
        await store.record_poll(
            observed_at=hour + timedelta(minutes=5),
            observations=(_observation(product_id),) if first else (),
            samples=(
                FundingSample(
                    product_id=product_id,
                    funding_time=hour,
                    rate=Decimal("0.00001"),
                    interval_seconds=3600,
                ),
            ),
            listing_fingerprint="sha256:" + "0" * 64,
            perpetual_count=1,
        )
        first = False
        hour += timedelta(hours=1)


def test_perp_backtest_binds_contract_margin_and_funding(tmp_path: Path) -> None:
    """The run binds recorded facts, simulates funding, and resubmits idempotently."""
    product_id = f"Z{uuid4().hex[:5].upper()}-20DEC30-CDE"
    dataset = write_dataset(tmp_path, product_id, "1h", start=DATA_START, count=_BARS, base=100)
    dataset_store = DatasetStore(tmp_path)

    async def exercise() -> None:
        engine = _engine()
        strategies = PostgresStrategyStore(engine)
        futures = PostgresFuturesObservationStore(engine, provider=f"t-{uuid4().hex[:12]}")
        record = await create_strategy_from_definition(strategies, _definition(product_id))
        try:
            snapshot = await strategies.snapshot(record.strategy_id)
            submitter = PostgresBacktestSubmitter(engine, dataset_store)
            request = _request(snapshot.strategy_fingerprint, dataset)
            await futures.record_poll(
                observed_at=_STARTS,
                observations=(_observation(product_id),),
                samples=(),
                listing_fingerprint="sha256:" + "0" * 64,
                perpetual_count=1,
            )
            with pytest.raises(BacktestSubmissionRejectedError, match="FUNDING_HISTORY_MISSING"):
                await submitter.submit(request)
            await _record_funding(futures, product_id, _ENDS)
            latest = await futures.latest_instrument(product_id)
            assert latest is not None
            assert latest[0] == _observation(product_id)

            published = await submitter.submit(request)
            runs = PostgresResearchRunStore(engine)
            run = await runs.load(published.run_fingerprint, dataset_store=dataset_store)
            specification = run.specification
            assert specification.instrument_contract is not None
            assert specification.instrument_contract.contract_size == "0.01"
            assert specification.margin is not None
            assert specification.margin.long_rate == "0.21025"
            assert specification.funding is not None
            assert (
                specification.funding.settled_hours
                == int((_ENDS - _STARTS).total_seconds()) // 3600
            )
            assert specification.costs.fee_per_contract == "0.15"
            result = await PostgresBacktestResultStore(
                engine, research_run_store=runs, dataset_store=dataset_store
            ).load(published.result_fingerprint)
            assert "futures_constant_margin" in (result.summary.validity_limits or ())
            assert result.summary.total_funding is not None
            assert await submitter.submit(request) == published
        finally:
            await strategies.delete(record.strategy_id)
            await dispose(engine)

    asyncio.run(exercise())
