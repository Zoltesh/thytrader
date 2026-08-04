"""Behavioral tests for immutable historical market-data datasets."""

from dataclasses import replace
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


def _candle_at(starts_at: datetime) -> Candle:
    """Build one exact hourly candle for an immutable dataset fixture."""
    return Candle(
        starts_at=starts_at,
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("12.5"),
    )


def _candle(hour: int) -> Candle:
    """Build one exact hourly candle on the fixture's first calendar day."""
    return _candle_at(datetime(2026, 7, 1, hour, tzinfo=UTC))


def _complete_report() -> CandleRangeReport:
    """Build one complete three-candle range report for storage behavior."""
    return analyze_range(
        (_candle(0), _candle(1), _candle(2)),
        CandleInterval.ONE_HOUR,
        starts_at=datetime(2026, 7, 1, 0, tzinfo=UTC),
        ends_at=datetime(2026, 7, 1, 3, tzinfo=UTC),
        now=datetime(2026, 7, 1, 4, tzinfo=UTC),
    )


def _extension_report(now: datetime) -> CandleRangeReport:
    """Build one complete overlapping range report that advances the fixture by two candles."""
    return analyze_range(
        tuple(_candle_at(datetime(2026, 7, 1, hour, tzinfo=UTC)) for hour in (2, 3, 4)),
        CandleInterval.ONE_HOUR,
        starts_at=datetime(2026, 7, 1, 2, tzinfo=UTC),
        ends_at=datetime(2026, 7, 1, 5, tzinfo=UTC),
        now=now,
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


def test_dataset_store_queries_verified_candles_by_fingerprint(tmp_path: Path) -> None:
    """Backtest callers can resolve exact typed candles from an immutable fingerprint."""
    store = DatasetStore(tmp_path)
    manifest = store.write("coinbase", "BTC-USD", _complete_report())

    candles = store.load_candles(manifest.content_fingerprint)

    assert candles == _complete_report().quality.candles


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


def test_dataset_store_rejects_forged_range_facts_before_publication(tmp_path: Path) -> None:
    """A forged complete report must not publish files before its range facts are recomputed."""
    report = _complete_report()
    forged_reports = (
        replace(report, requested_candle_count=report.requested_candle_count + 1),
        replace(
            report,
            quality=replace(report.quality, gap_count=report.quality.gap_count + 1),
        ),
        replace(report, starts_at=report.starts_at - CandleInterval.ONE_HOUR.duration),
    )

    for index, forged_report in enumerate(forged_reports):
        root = tmp_path / f"case-{index}"
        with pytest.raises(DatasetStoreError, match=r"report facts|coverage"):
            DatasetStore(root).write("coinbase", "BTC-USD", forged_report)
        assert not tuple(root.rglob("*.parquet"))
        assert not tuple((root / "manifests").glob("*.json"))


def test_dataset_store_rejects_forged_extension_report_before_publication(tmp_path: Path) -> None:
    """An extension must validate its incremental report before writing replacement partitions."""
    store = DatasetStore(tmp_path)
    prior = store.write("coinbase", "BTC-USD", _complete_report())
    incremental = _extension_report(datetime(2026, 7, 1, 6, tzinfo=UTC))
    forged = replace(
        incremental,
        requested_candle_count=incremental.requested_candle_count + 1,
    )

    with pytest.raises(DatasetStoreError, match="report facts"):
        store.extend(prior.content_fingerprint, forged)

    assert tuple(tmp_path.rglob("*.parquet")) == prior.files
    assert tuple((tmp_path / "manifests").glob("*.json")) == (prior.manifest_path,)


def test_dataset_store_extends_valid_stale_report(tmp_path: Path) -> None:
    """Publication validation must ignore stale status while preserving all durable range facts."""
    store = DatasetStore(tmp_path)
    prior = store.write("coinbase", "BTC-USD", _complete_report())
    stale_report = _extension_report(datetime(2026, 7, 10, tzinfo=UTC))

    assert stale_report.quality.is_stale is True
    extended = store.extend(prior.content_fingerprint, stale_report)

    assert extended.ends_at == "2026-07-01T05:00:00Z"
    assert extended.expected_candle_count == 5
    assert len(store.load_candles(extended.content_fingerprint)) == 5


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


def test_dataset_store_rejects_forged_report_with_invalid_ohlcv_values(tmp_path: Path) -> None:
    """The durable write boundary must not trust a forged complete report's candle values."""
    valid_report = _complete_report()
    invalid_report = replace(
        valid_report,
        quality=replace(
            valid_report.quality,
            candles=(
                replace(valid_report.quality.candles[0], high=Decimal("Infinity")),
                *valid_report.quality.candles[1:],
            ),
        ),
    )

    with pytest.raises(DatasetStoreError, match="Candle"):
        DatasetStore(tmp_path).write("coinbase", "BTC-USD", invalid_report)


def test_dataset_store_rejects_nonfinite_values_during_verified_load(tmp_path: Path) -> None:
    """Verified reads must reject persisted Infinity or NaN even when Parquet schema is intact."""
    store = DatasetStore(tmp_path)
    manifest = store.write("coinbase", "BTC-USD", _complete_report())
    rows = [dict(row) for row in pl.read_parquet(manifest.files[0]).to_dicts()]
    rows[0]["high"] = "Infinity"
    rows[1]["volume"] = "NaN"
    pl.DataFrame(rows).write_parquet(manifest.files[0])

    with pytest.raises(DatasetStoreError, match="verification"):
        store.load_candles(manifest.content_fingerprint)


def test_dataset_store_load_verified_detects_manifested_parquet_tampering(tmp_path: Path) -> None:
    """A future reader must reject a manifest whose immutable Parquet content was modified."""
    store = DatasetStore(tmp_path)
    manifest = store.write("coinbase", "BTC-USD", _complete_report())
    manifest.files[0].write_bytes(b"not a parquet dataset")

    with pytest.raises(DatasetStoreError, match="verification"):
        store.load_verified(manifest.manifest_path)


def test_dataset_store_rejects_manifest_with_duplicate_file_entries(tmp_path: Path) -> None:
    """A manifest listing the same file twice must fail verification via duplicate candles."""
    store = DatasetStore(tmp_path)
    written = store.write("coinbase", "BTC-USD", _complete_report())
    manifest_body = json.loads(written.manifest_path.read_text())
    manifest_body["files"] = manifest_body["files"] + manifest_body["files"]
    written.manifest_path.write_text(json.dumps(manifest_body))

    with pytest.raises(DatasetStoreError, match="verification"):
        store.load_verified(written.manifest_path)


def test_dataset_store_write_is_idempotent_for_identical_range(tmp_path: Path) -> None:
    """Writing the same complete range twice must return the same fingerprint and manifest path."""
    store = DatasetStore(tmp_path)
    first = store.write("coinbase", "BTC-USD", _complete_report())
    second = store.write("coinbase", "BTC-USD", _complete_report())

    assert first.content_fingerprint == second.content_fingerprint
    assert first.manifest_path == second.manifest_path


def test_dataset_store_rejects_write_when_orphan_partition_exists(tmp_path: Path) -> None:
    """A partition from a crashed write must cause a safe failure rather than silent reuse."""
    store = DatasetStore(tmp_path)
    written = store.write("coinbase", "BTC-USD", _complete_report())
    written.manifest_path.unlink()

    with pytest.raises(DatasetStoreError, match="already exists without a verified"):
        store.write("coinbase", "BTC-USD", _complete_report())


def test_dataset_store_rejects_manifest_with_negative_counts(tmp_path: Path) -> None:
    """Negative count facts must be rejected as inconsistent with a valid historical range."""
    store = DatasetStore(tmp_path)
    written = store.write("coinbase", "BTC-USD", _complete_report())
    manifest_body = json.loads(written.manifest_path.read_text())
    manifest_body["expected_candle_count"] = -1
    written.manifest_path.write_text(json.dumps(manifest_body))

    with pytest.raises(DatasetStoreError, match="inconsistent"):
        store.load_verified(written.manifest_path)
