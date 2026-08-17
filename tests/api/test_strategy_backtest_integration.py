"""Real PostgreSQL coverage for the browser strategy-to-backtest workflow."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import delete

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import Candle, CandleInterval
from thytrader.market_data.quality import analyze_range
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.schema import (
    published_backtest_results,
    published_research_run_specs,
    published_strategy_versions,
    strategy_dataset_bindings,
)

if TYPE_CHECKING:
    from pathlib import Path

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")


@pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)
def test_browser_strategy_workflow_publishes_and_reuses_immutable_backtest(
    tmp_path: Path,
) -> None:
    """Create, publish, submit, inspect, and resubmit through real API and storage boundaries."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    manifest = _write_dataset(tmp_path)
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(_TEST_DATABASE_URL),
        market_data_dataset_root=tmp_path,
    )
    strategy_fingerprint: str | None = None
    run_fingerprint: str | None = None
    result_fingerprint: str | None = None
    try:
        with TestClient(create_app(settings)) as client:
            draft_response = client.post("/api/v1/strategies")
            assert draft_response.status_code == 201
            draft = draft_response.json()["strategy"]
            revision = draft_response.json()["revision"]

            published_response = client.post(
                f"/api/v1/strategies/{draft['strategy_id']}/publish",
                json={"strategy": draft, "revision": revision},
            )
            assert published_response.status_code == 201
            published = published_response.json()
            strategy_fingerprint = published["strategy_fingerprint"]

            request = {
                "strategy_fingerprint": strategy_fingerprint,
                "dataset_fingerprint": manifest,
                "evaluation_start": "2026-07-10T00:00:00Z",
                "evaluation_end": "2026-07-20T00:00:00Z",
                "initial_quote_balance": "10000",
                "maker_fee_rate": "0.001",
                "taker_fee_rate": "0.002",
                "fixed_slippage_bps": "10",
            }
            first = client.post("/api/v1/backtests", json=request)
            assert first.status_code == 201, first.text
            first_payload = first.json()
            run_fingerprint = first_payload["run_fingerprint"]
            result_fingerprint = first_payload["result_fingerprint"]

            detail = client.get(f"/api/v1/backtests/{result_fingerprint}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["result_fingerprint"] == result_fingerprint
            assert detail.json()["result"]["run_fingerprint"] == run_fingerprint
            assert detail.json()["result"]["strategy_fingerprint"] == strategy_fingerprint
            assert detail.json()["result"]["dataset_fingerprint"] == manifest

            second = client.post("/api/v1/backtests", json=request)
            assert second.status_code == 201, second.text
            assert second.json() == first_payload
    finally:
        asyncio.run(
            _cleanup(
                _TEST_DATABASE_URL,
                strategy_fingerprint=strategy_fingerprint,
                run_fingerprint=run_fingerprint,
                result_fingerprint=result_fingerprint,
            )
        )


def _write_dataset(root: Path) -> str:
    """Publish enough verified hourly coverage for warmup and a ten-day evaluation window."""
    starts_at = datetime(2026, 7, 7, 22, tzinfo=UTC)
    candle_count = 291
    candles = tuple(
        Candle(
            starts_at=starts_at + timedelta(hours=index),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("12.5"),
        )
        for index in range(candle_count)
    )
    ends_at = starts_at + timedelta(hours=candle_count)
    report = analyze_range(candles, CandleInterval.ONE_HOUR, starts_at, ends_at, now=ends_at)
    return DatasetStore(root).write("coinbase", "BTC-USD", report).content_fingerprint


async def _cleanup(
    database_url: str,
    *,
    strategy_fingerprint: str | None,
    run_fingerprint: str | None,
    result_fingerprint: str | None,
) -> None:
    """Remove only immutable records created by this test in foreign-key order."""
    engine = create_engine(SecretStr(database_url))
    try:
        async with engine.begin() as connection:
            if result_fingerprint is not None:
                await connection.execute(
                    delete(published_backtest_results).where(
                        published_backtest_results.c.result_fingerprint == result_fingerprint
                    )
                )
            if run_fingerprint is not None:
                await connection.execute(
                    delete(published_research_run_specs).where(
                        published_research_run_specs.c.run_fingerprint == run_fingerprint
                    )
                )
            if strategy_fingerprint is not None:
                await connection.execute(
                    delete(strategy_dataset_bindings).where(
                        strategy_dataset_bindings.c.strategy_fingerprint == strategy_fingerprint
                    )
                )
                await connection.execute(
                    delete(published_strategy_versions).where(
                        published_strategy_versions.c.strategy_fingerprint == strategy_fingerprint
                    )
                )
    finally:
        await dispose(engine)
