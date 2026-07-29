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


def test_dataset_store_fingerprint_includes_dataset_identity(tmp_path: Path) -> None:
    """Identical candle content for distinct products must never share a manifest identity."""
    store = DatasetStore(tmp_path)

    bitcoin = store.write("coinbase", "BTC-USD", _complete_report())
    ethereum = store.write("coinbase", "ETH-USD", _complete_report())

    assert bitcoin.content_fingerprint != ethereum.content_fingerprint
    assert bitcoin.manifest_path != ethereum.manifest_path


def test_dataset_store_rejects_path_traversal_identifiers(tmp_path: Path) -> None:
    """Dataset identifiers must not escape the configured local dataset root."""
    with pytest.raises(DatasetStoreError, match="identifier"):
        DatasetStore(tmp_path).write("../coinbase", "BTC-USD", _complete_report())


def test_dataset_store_rejects_manifest_path_traversal(tmp_path: Path) -> None:
    """An untrusted manifest must not redirect the verified reader outside the dataset root."""
    fingerprint = "0" * 64
    manifest_path = tmp_path / "manifests" / f"{fingerprint}.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "coinbase",
                "product_id": "BTC-USD",
                "timeframe": "1h",
                "starts_at": "2026-07-01T00:00:00Z",
                "ends_at": "2026-07-01T03:00:00Z",
                "expected_candle_count": 3,
                "received_candle_count": 3,
                "gap_count": 0,
                "missing_intervals": 0,
                "complete": True,
                "content_fingerprint": f"sha256:{fingerprint}",
                "files": ["../outside.parquet"],
            }
        )
    )

    with pytest.raises(DatasetStoreError, match="escapes its root"):
        DatasetStore(tmp_path).load_verified(manifest_path)


def test_dataset_store_rejects_manifest_paths_that_escape_through_symlinks(tmp_path: Path) -> None:
    """A relative manifest path must not reach outside the root through an in-root symlink."""
    outside_root = tmp_path.parent / "outside-dataset-root"
    outside_root.mkdir()
    (tmp_path / "escape").symlink_to(outside_root, target_is_directory=True)
    fingerprint = "1" * 64
    manifest_path = tmp_path / "manifests" / f"{fingerprint}.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "coinbase",
                "product_id": "BTC-USD",
                "timeframe": "1h",
                "starts_at": "2026-07-01T00:00:00Z",
                "ends_at": "2026-07-01T03:00:00Z",
                "expected_candle_count": 3,
                "received_candle_count": 3,
                "gap_count": 0,
                "missing_intervals": 0,
                "complete": True,
                "content_fingerprint": f"sha256:{fingerprint}",
                "files": ["escape/outside.parquet"],
            }
        )
    )

    with pytest.raises(DatasetStoreError, match="escapes its root"):
        DatasetStore(tmp_path).load_verified(manifest_path)


def test_dataset_store_rejects_incomplete_manifest_even_when_it_is_well_formed(
    tmp_path: Path,
) -> None:
    """A verified reader must reject manifests that cannot be valid backtest dataset candidates."""
    store = DatasetStore(tmp_path)
    written = store.write("coinbase", "BTC-USD", _complete_report())
    manifest_body = json.loads(written.manifest_path.read_text())
    manifest_body["complete"] = False
    written.manifest_path.write_text(json.dumps(manifest_body))

    with pytest.raises(DatasetStoreError, match="complete"):
        store.load_verified(written.manifest_path)


def test_dataset_store_rejects_manifest_outside_canonical_publication_path(tmp_path: Path) -> None:
    """A reader must only trust the manifest path selected by the content fingerprint."""
    store = DatasetStore(tmp_path)
    written = store.write("coinbase", "BTC-USD", _complete_report())
    copied_manifest = tmp_path / "unpublished-copy.json"
    copied_manifest.write_text(written.manifest_path.read_text())

    with pytest.raises(DatasetStoreError, match="canonical publication path"):
        store.load_verified(copied_manifest)


def test_dataset_store_rejects_boolean_manifest_counts(tmp_path: Path) -> None:
    """JSON booleans must not satisfy integer dataset count fields."""
    store = DatasetStore(tmp_path)
    written = store.write("coinbase", "BTC-USD", _complete_report())
    manifest_body = json.loads(written.manifest_path.read_text())
    manifest_body["expected_candle_count"] = True
    written.manifest_path.write_text(json.dumps(manifest_body))

    with pytest.raises(DatasetStoreError, match="facts are malformed"):
        store.load_verified(written.manifest_path)


def test_dataset_store_load_verified_returns_a_healthy_published_dataset(tmp_path: Path) -> None:
    """A successfully published manifest must verify and preserve its immutable identity."""
    store = DatasetStore(tmp_path)
    written = store.write("coinbase", "BTC-USD", _complete_report())

    loaded = store.load_verified(written.manifest_path)

    assert loaded.content_fingerprint == written.content_fingerprint
    assert loaded.files == written.files


def test_dataset_store_load_verified_detects_manifested_parquet_tampering(tmp_path: Path) -> None:
    """A future reader must reject a manifest whose immutable Parquet content was modified."""
    store = DatasetStore(tmp_path)
    manifest = store.write("coinbase", "BTC-USD", _complete_report())
    manifest.files[0].write_bytes(b"not a parquet dataset")

    with pytest.raises(DatasetStoreError, match="verification"):
        store.load_verified(manifest.manifest_path)
