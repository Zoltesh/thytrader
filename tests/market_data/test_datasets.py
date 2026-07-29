"""Behavioral tests for immutable historical market-data datasets."""

from datetime import UTC, datetime
from decimal import Decimal
import json
from typing import TYPE_CHECKING

import polars as pl
import pytest

from thytrader.market_data.datasets import DatasetStore, DatasetStoreError
from thytrader.market_data.models import Candle, CandleInterval, CandleRangeReport
from thytrader.market_data.quality import analyze_range

if TYPE_CHECKING:
    from pathlib import Path


def _candle(hour: int) -> Candle:
    """Build one exact hourly candle for an immutable dataset fixture."""
    return Candle(
        starts_at=datetime(2026, 7, 1, hour, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("12.5"),
    )


def _complete_report() -> CandleRangeReport:
    """Build one complete three-candle range report for storage behavior."""
    return analyze_range(
        (_candle(0), _candle(1), _candle(2)),
        CandleInterval.ONE_HOUR,
        starts_at=datetime(2026, 7, 1, 0, tzinfo=UTC),
        ends_at=datetime(2026, 7, 1, 3, tzinfo=UTC),
        now=datetime(2026, 7, 1, 4, tzinfo=UTC),
    )


def test_dataset_store_writes_complete_range_as_parquet_with_manifest(tmp_path: Path) -> None:
    """A complete range must produce a partitioned Parquet file and fingerprinted JSON manifest."""
    manifest = DatasetStore(tmp_path).write("coinbase", "BTC-USD", _complete_report())

    assert manifest.complete is True
    assert len(manifest.files) == 1
    assert manifest.content_fingerprint.startswith("sha256:")
    assert manifest.manifest_path.is_file()
    manifest_body = json.loads(manifest.manifest_path.read_text())
    assert manifest_body["schema_version"] == 1
    assert manifest_body["content_fingerprint"] == manifest.content_fingerprint
    assert manifest_body["complete"] is True
    assert manifest.files[0].is_file()
    assert manifest.files[0].relative_to(tmp_path).parts[:5] == (
        "coinbase",
        "BTC-USD",
        "1h",
        "2026",
        "07",
    )
    frame = pl.read_parquet(manifest.files[0])
    assert frame.columns == ["starts_at", "open", "high", "low", "close", "volume"]
    assert frame.height == 3


def test_dataset_store_rejects_incomplete_range(tmp_path: Path) -> None:
    """An incomplete range must never become a durable backtest candidate."""
    report = analyze_range(
        (_candle(0), _candle(2)),
        CandleInterval.ONE_HOUR,
        starts_at=datetime(2026, 7, 1, 0, tzinfo=UTC),
        ends_at=datetime(2026, 7, 1, 3, tzinfo=UTC),
        now=datetime(2026, 7, 1, 4, tzinfo=UTC),
    )

    with pytest.raises(DatasetStoreError, match="complete"):
        DatasetStore(tmp_path).write("coinbase", "BTC-USD", report)
