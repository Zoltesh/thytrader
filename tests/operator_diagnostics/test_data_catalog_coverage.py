"""Catalog honesty: completeness is relative to the watch lookback (ADR 0095)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from thytrader.data_control.service import worker_state_payload
from thytrader.market_data.datasets import DatasetManifest
from thytrader.market_data.models import CandleInterval
from thytrader.market_data.watchlist import MarketDataWatchTarget
from thytrader.market_data.worker_state import MarketDataWorkerState, MarketDataWorkerStatus
from thytrader.operator.service import _merge_coverage_rows

_NOW = datetime(2026, 10, 2, 11, 13, 30, tzinfo=UTC)
_MINUTE = CandleInterval.ONE_MINUTE
_FINGERPRINT = "sha256:" + "b" * 64


def _bonk_state(
    *,
    starts_at: datetime,
    ends_at: datetime,
    floor: datetime | None = None,
) -> MarketDataWorkerState:
    """Return the BONK-USD 1m worker row the bug report showed: a verified short island."""
    bars = int((ends_at - starts_at) / _MINUTE.duration)
    return MarketDataWorkerState(
        provider="coinbase",
        product_id="BONK-USD",
        timeframe=_MINUTE,
        status=MarketDataWorkerStatus.SUCCEEDED,
        last_attempt_at=_NOW,
        last_success_at=_NOW,
        requested_starts_at=starts_at,
        requested_ends_at=ends_at,
        covered_starts_at=starts_at,
        covered_ends_at=ends_at,
        expected_candle_count=bars,
        received_candle_count=bars,
        gap_count=0,
        missing_intervals=0,
        complete=True,
        content_fingerprint=_FINGERPRINT,
        failure_code=None,
        failure_message=None,
        consecutive_failures=0,
        updated_at=_NOW,
        history_floor_at=floor,
    )


def _watch(lookback_hours: int) -> MarketDataWatchTarget:
    """Return an enabled BONK-USD 1m watch."""
    return MarketDataWatchTarget(
        provider="coinbase",
        product_id="BONK-USD",
        timeframe=_MINUTE,
        lookback_hours=lookback_hours,
        enabled=True,
        updated_at=_NOW,
    )


def test_a_two_minute_dataset_for_a_ninety_day_watch_is_not_complete() -> None:
    """The reproduced BONK row reports coverage 2 of 129,600 and is backfilling."""
    state = _bonk_state(
        starts_at=datetime(2026, 10, 2, 10, 57, tzinfo=UTC),
        ends_at=datetime(2026, 10, 2, 10, 59, tzinfo=UTC),
    )

    (row,) = _merge_coverage_rows(_NOW, (_watch(2160),), (state,), ())

    assert row.island_complete is True
    assert row.complete is False
    assert row.watch_complete is False
    assert row.watch_status == "backfilling"
    assert row.sparsity == "none"
    assert row.watch_sparsity == "gapped"
    assert row.watch_covered_candle_count == 2
    assert row.watch_expected_candle_count == 129_600
    assert row.watch_coverage_ratio == 0.0
    payload = worker_state_payload(state, lookback_hours=2160, interval=_MINUTE, now=_NOW)
    assert payload["complete"] is False
    assert payload["island_complete"] is True
    assert payload["watch_covered_candle_count"] == 2


def test_a_floor_written_before_the_repair_reads_complete_until_cleared() -> None:
    """The pre-ADR 0095 floor at the island start made the same row look done.

    Migration 0059 clears such floors; afterwards the row is honest again.
    """
    starts_at = datetime(2026, 10, 2, 10, 57, tzinfo=UTC)
    pinned = _bonk_state(
        starts_at=starts_at, ends_at=datetime(2026, 10, 2, 11, 13, tzinfo=UTC), floor=starts_at
    )

    (before,) = _merge_coverage_rows(_NOW, (_watch(2160),), (pinned,), ())
    (after,) = _merge_coverage_rows(
        _NOW, (_watch(2160),), (replace(pinned, history_floor_at=None),), ()
    )

    assert before.watch_complete is True
    assert after.watch_complete is False
    assert after.complete is False
    assert after.watch_covered_candle_count == 16


def test_full_coverage_reports_its_no_trade_bars() -> None:
    """A whole sparse series is complete and names how many bars were no-trade bars."""
    closed_end = _MINUTE.align_closed_end(_NOW)
    starts_at = closed_end - timedelta(hours=6)
    state = _bonk_state(starts_at=starts_at, ends_at=closed_end)
    manifest = DatasetManifest(
        provider="coinbase",
        product_id="BONK-USD",
        timeframe="1m",
        starts_at=starts_at.isoformat().replace("+00:00", "Z"),
        ends_at=closed_end.isoformat().replace("+00:00", "Z"),
        expected_candle_count=360,
        received_candle_count=360,
        gap_count=0,
        missing_intervals=0,
        complete=True,
        content_fingerprint=_FINGERPRINT,
        files=(),
        manifest_path=Path("manifests/b.json"),
        synthetic_no_trade_intervals=144,
    )

    (row,) = _merge_coverage_rows(_NOW, (_watch(6),), (state,), (manifest,))

    assert row.complete is True
    assert row.watch_complete is True
    assert row.watch_covered_candle_count == 360
    assert row.watch_expected_candle_count == 360
    assert row.watch_coverage_ratio == 1.0
    assert row.synthetic_no_trade_intervals == 144
