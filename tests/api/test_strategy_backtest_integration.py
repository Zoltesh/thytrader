"""Real PostgreSQL coverage for the browser strategy-to-backtest workflow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import Candle, CandleInterval
from thytrader.market_data.quality import analyze_range

if TYPE_CHECKING:
    from pathlib import Path

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")


@pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)
def test_browser_strategy_workflow_snapshots_backtests_and_deletes(tmp_path: Path) -> None:
    """Create, backtest by id, edit, spot the earlier edit, and delete through real storage."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    manifest = _write_dataset(tmp_path)
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(_TEST_DATABASE_URL),
        market_data_dataset_root=tmp_path,
    )
    strategy_id: str | None = None
    with TestClient(create_app(settings)) as client:
        try:
            created = client.post("/api/v1/strategies")
            assert created.status_code == 201, created.text
            strategy_id = created.json()["strategy_id"]
            current = created.json()["current_fingerprint"]
            request = {
                "strategy_id": strategy_id,
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
            assert first_payload["strategy_fingerprint"] == current
            result_fingerprint = first_payload["result_fingerprint"]

            detail = client.get(f"/api/v1/backtests/{result_fingerprint}?detail=full")
            assert detail.status_code == 200, detail.text
            assert detail.json()["result"]["run_fingerprint"] == first_payload["run_fingerprint"]
            assert detail.json()["result"]["strategy_fingerprint"] == current
            assert detail.json()["result"]["dataset_fingerprint"] == manifest
            assert detail.json()["costs"] == {
                "maker_fee_rate": "0.001",
                "taker_fee_rate": "0.002",
                "fixed_slippage_bps": "10",
                "spread_bps": "0",
            }
            assert detail.json()["result"]["engine"] == "thytrader-backtest"

            second = client.post("/api/v1/backtests", json=request)
            assert second.status_code == 201, second.text
            assert second.json() == first_payload

            document = dict(created.json()["document"])
            document["name"] = "Edited after the backtest"
            saved = client.put(
                f"/api/v1/strategies/{strategy_id}", json={"document": document, "revision": 1}
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["current_fingerprint"] != current

            listed = client.get(f"/api/v1/backtests?strategy_id={strategy_id}").json()
            assert [entry["result_fingerprint"] for entry in listed["entries"]] == [
                result_fingerprint
            ]
            assert listed["entries"][0]["strategy_id"] == strategy_id
            earlier = client.get(f"/api/v1/strategies/snapshots/{current}").json()
            assert earlier["is_current"] is False
            assert earlier["strategy_id"] == strategy_id
            library = client.get("/api/v1/strategies?limit=100").json()
            row = next(item for item in library["strategies"] if item["strategy_id"] == strategy_id)
            assert row["backtest"]["result_fingerprint"] == result_fingerprint
            assert row["backtest"]["strategy_fingerprint"] == current
        finally:
            if strategy_id is not None:
                deleted = client.delete(f"/api/v1/strategies/{strategy_id}")
                assert deleted.status_code == 200, deleted.text
                assert deleted.json()["counts"]["backtests"] == 1
                assert deleted.json()["counts"]["research_runs"] == 1
        gone = client.get(f"/api/v1/backtests?strategy_id={strategy_id}").json()
        assert gone["entries"] == []
        missing = client.get(f"/api/v1/backtests/{first_payload['result_fingerprint']}")
        assert missing.status_code == 404


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
