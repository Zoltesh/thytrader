"""Behavioral tests for bounded garbage collection of superseded dataset revisions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from typing import TYPE_CHECKING

import pytest

from thytrader.market_data.dataset_retention import (
    RetentionReport,
    collect_superseded_datasets,
)
from thytrader.market_data.datasets import DatasetManifest, DatasetStore
from thytrader.market_data.models import Candle, CandleInterval, CandleRangeReport
from thytrader.market_data.quality import analyze_range
from thytrader.market_data_worker.retention import DatasetRetentionRunner
from thytrader.persistence.audit_events import InMemoryAuditEventStore

if TYPE_CHECKING:
    from pathlib import Path

_NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
_OLD = _NOW - timedelta(days=3)


class _References:
    """Fixed reference set, or a failing scan."""

    def __init__(self, fingerprints: frozenset[str] = frozenset(), *, fail: bool = False) -> None:
        """Store the configured references."""
        self._fingerprints = fingerprints
        self._fail = fail

    async def referenced_fingerprints(self) -> frozenset[str]:
        """Return the configured set or raise like an unavailable database."""
        if self._fail:
            raise RuntimeError("database unavailable")
        return self._fingerprints


def _report(starts_at: datetime, hours: int) -> CandleRangeReport:
    """Build one complete hourly report."""
    candles = tuple(
        Candle(
            starts_at=starts_at + timedelta(hours=index),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("1"),
        )
        for index in range(hours)
    )
    ends_at = starts_at + timedelta(hours=hours)
    return analyze_range(candles, CandleInterval.ONE_HOUR, starts_at, ends_at, now=ends_at)


def _age(manifest: DatasetManifest, when: datetime) -> None:
    """Set a manifest publication instant (mtime)."""
    stamp = when.timestamp()
    os.utime(manifest.manifest_path, (stamp, stamp))


def _lineage(store: DatasetStore, start: datetime, days: int) -> list[DatasetManifest]:
    """Publish one island day by day, like chunked ingest, each revision older than grace."""
    revisions = [store.write("coinbase", "BTC-USD", _report(start, 24))]
    for day in range(1, days):
        overlap = start + timedelta(days=day) - timedelta(hours=1)
        revisions.append(store.extend(revisions[-1].content_fingerprint, _report(overlap, 25)))
    for index, revision in enumerate(revisions):
        _age(revision, _OLD + timedelta(minutes=index))
    return revisions


def _run(
    root: Path,
    references: _References,
    *,
    dry_run: bool = False,
    max_manifests: int = 100,
) -> RetentionReport:
    """Run one retention pass with full verification."""
    store = DatasetStore(root)

    def verify(path: Path) -> bool:
        store.load_verified(path)
        return True

    return asyncio.run(
        collect_superseded_datasets(
            root=root,
            references=references,
            verify=verify,
            now=_NOW,
            dry_run=dry_run,
            max_manifests=max_manifests,
        )
    )


def test_superseded_revisions_are_deleted_and_newest_stays_verified(tmp_path: Path) -> None:
    """Only the newest revision of a lineage survives; shared partitions are kept."""
    store = DatasetStore(tmp_path)
    revisions = _lineage(store, datetime(2026, 9, 1, tzinfo=UTC), 4)
    report = _run(tmp_path, _References())
    assert report.aborted is None
    assert report.deleted_manifests == 3
    assert report.deleted_by_target == {"coinbase/BTC-USD/1h": 3}
    remaining = sorted(path.stem for path in (tmp_path / "manifests").glob("*.json"))
    assert remaining == [revisions[-1].content_fingerprint.removeprefix("sha256:")]
    candles = DatasetStore(tmp_path).load_candles(revisions[-1].content_fingerprint)
    assert len(candles) == 96
    assert report.deleted_files > 0
    assert report.freed_bytes > 0


def test_referenced_revision_is_never_deleted(tmp_path: Path) -> None:
    """A fingerprint bound by any stored record survives even when superseded."""
    store = DatasetStore(tmp_path)
    revisions = _lineage(store, datetime(2026, 9, 1, tzinfo=UTC), 3)
    pinned = revisions[0].content_fingerprint
    report = _run(tmp_path, _References(frozenset({pinned})))
    assert report.deleted_manifests == 1
    assert DatasetStore(tmp_path).load_manifest(pinned).content_fingerprint == pinned
    assert len(DatasetStore(tmp_path).load_candles(pinned)) == 24


def test_older_island_before_a_hole_is_kept(tmp_path: Path) -> None:
    """The final revision of an older island is maximal, so it is not garbage."""
    store = DatasetStore(tmp_path)
    old_island = _lineage(store, datetime(2026, 9, 1, tzinfo=UTC), 2)
    new_island = _lineage(store, datetime(2026, 9, 10, tzinfo=UTC), 2)
    report = _run(tmp_path, _References())
    assert report.deleted_manifests == 2
    remaining = {path.stem for path in (tmp_path / "manifests").glob("*.json")}
    assert remaining == {
        old_island[-1].content_fingerprint.removeprefix("sha256:"),
        new_island[-1].content_fingerprint.removeprefix("sha256:"),
    }


def test_recently_superseded_revision_waits_for_grace(tmp_path: Path) -> None:
    """A revision that was latest moments ago stays until its supersession is old enough."""
    store = DatasetStore(tmp_path)
    revisions = _lineage(store, datetime(2026, 9, 1, tzinfo=UTC), 3)
    _age(revisions[1], _NOW - timedelta(hours=1))
    _age(revisions[2], _NOW - timedelta(minutes=5))
    report = _run(tmp_path, _References())
    assert report.deleted_manifests == 0
    assert report.eligible_manifests == 0


def _data_files(root: Path) -> list[str]:
    """List dataset files, ignoring the retention advisory lock."""
    return sorted(
        path.name for path in root.rglob("*") if path.is_file() and path.name != ".retention.lock"
    )


def test_dry_run_reports_without_deleting(tmp_path: Path) -> None:
    """The default one-shot mode changes nothing on disk."""
    store = DatasetStore(tmp_path)
    _lineage(store, datetime(2026, 9, 1, tzinfo=UTC), 3)
    before = _data_files(tmp_path)
    report = _run(tmp_path, _References(), dry_run=True)
    assert report.eligible_manifests == 2
    assert report.deleted_manifests == 0
    assert _data_files(tmp_path) == before


def test_unreadable_manifest_or_reference_failure_aborts_pass(tmp_path: Path) -> None:
    """Unknown publication or reference facts must delete nothing."""
    store = DatasetStore(tmp_path)
    _lineage(store, datetime(2026, 9, 1, tzinfo=UTC), 3)
    assert _run(tmp_path, _References(fail=True)).aborted == "reference_scan_failed"
    (tmp_path / "manifests" / f"{'f' * 64}.json").write_text("{not json", encoding="utf-8")
    report = _run(tmp_path, _References())
    assert report.aborted == "unreadable_manifests"
    assert report.deleted_manifests == 0
    assert len(list((tmp_path / "manifests").glob("*.json"))) == 4


def test_pass_is_bounded_and_runner_continues_when_truncated(tmp_path: Path) -> None:
    """A capped pass deletes oldest-first, and the worker runner reruns it next cycle."""
    store = DatasetStore(tmp_path)
    revisions = _lineage(store, datetime(2026, 9, 1, tzinfo=UTC), 5)
    first = _run(tmp_path, _References(), max_manifests=2)
    assert first.deleted_manifests == 2
    assert first.truncated is True
    assert not revisions[0].manifest_path.exists()
    assert revisions[2].manifest_path.exists()

    audit = InMemoryAuditEventStore()
    runner = DatasetRetentionRunner(
        root=tmp_path, references=_References(), audit_store=audit, max_manifests=1
    )
    second = asyncio.run(runner.maybe_run(_NOW))
    assert second is not None
    assert second.truncated is True
    third = asyncio.run(runner.maybe_run(_NOW + timedelta(seconds=1)))
    assert third is not None
    assert third.deleted_manifests == 1
    assert third.truncated is False
    assert asyncio.run(runner.maybe_run(_NOW + timedelta(seconds=2))) is None
    events = asyncio.run(audit.list_recent())
    assert [event.action for event in events] == ["dataset_retention", "dataset_retention"]
    assert "deleted_manifests=1" in events[0].detail


def test_retention_rejects_non_positive_bound(tmp_path: Path) -> None:
    """The per-pass bound must be positive."""
    with pytest.raises(ValueError, match="positive"):
        _run(tmp_path, _References(), max_manifests=0)
