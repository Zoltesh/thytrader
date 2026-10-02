"""No-trade bars: flat fills, manifest counts, and verification (ADR 0095)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from typing import TYPE_CHECKING

import pytest

from thytrader.market_data.datasets import DatasetStore, DatasetStoreError
from thytrader.market_data.models import Candle, CandleInterval, CandleRangeReport
from thytrader.market_data.no_trade import (
    count_no_trade_bars,
    fill_no_trade_gaps,
    has_interior_gaps,
    is_no_trade_bar,
    merge_confirmed_candles,
    no_trade_bar,
)
from thytrader.market_data.quality import analyze_range

if TYPE_CHECKING:
    from pathlib import Path

_HOUR = CandleInterval.ONE_HOUR
_START = datetime(2026, 9, 30, 22, tzinfo=UTC)


def _traded(offset: int, close: str = "105") -> Candle:
    """Build one traded hourly bar ``offset`` hours after the fixture start."""
    price = Decimal(close)
    return Candle(
        starts_at=_START + timedelta(hours=offset),
        open=price - Decimal(1),
        high=price + Decimal(2),
        low=price - Decimal(3),
        close=price,
        volume=Decimal("4.5"),
    )


def test_fill_inserts_flat_bars_between_trades_and_never_before_the_first() -> None:
    """Interior and trailing gaps get flat bars at the previous close; leading ones do not."""
    candles = (_traded(1, "105"), _traded(4, "111"))

    filled = fill_no_trade_gaps(candles, _HOUR, through=_START + timedelta(hours=7))

    assert [candle.starts_at for candle in filled] == [
        _START + timedelta(hours=offset) for offset in range(1, 7)
    ]
    assert [candle.close for candle in filled] == [
        Decimal("105"),
        Decimal("105"),
        Decimal("105"),
        Decimal("111"),
        Decimal("111"),
        Decimal("111"),
    ]
    assert [is_no_trade_bar(candle) for candle in filled] == [
        False,
        True,
        True,
        False,
        True,
        True,
    ]
    assert count_no_trade_bars(filled) == 4
    flat = filled[1]
    assert flat.open == flat.high == flat.low == flat.close == Decimal("105")
    assert fill_no_trade_gaps((), _HOUR) == ()


def test_interior_gap_detection_and_confirmation_merge() -> None:
    """A bar is missing only when both responses omit it; the confirmation's values win."""
    first = (_traded(0), _traded(2))
    revised = Candle(
        starts_at=_START + timedelta(hours=2),
        open=Decimal("1"),
        high=Decimal("3"),
        low=Decimal("1"),
        close=Decimal("2"),
        volume=Decimal("1"),
    )
    confirmation = (_traded(1), revised)

    merged = merge_confirmed_candles(first, confirmation)

    assert has_interior_gaps(first, _HOUR) is True
    assert has_interior_gaps(merged, _HOUR) is False
    assert merged[2] == revised


def _filled_report() -> CandleRangeReport:
    """Return a complete 24-bar report whose second and third bars are no-trade bars."""
    real = (_traded(0, "100"), *(_traded(offset, "101") for offset in range(3, 24)))
    candles = fill_no_trade_gaps(real, _HOUR)
    ends_at = _START + timedelta(hours=24)
    return analyze_range(candles, _HOUR, _START, ends_at, ends_at)


def test_manifest_counts_no_trade_bars_only_when_present(tmp_path: Path) -> None:
    """The count is stored when non-zero and omitted otherwise, outside the fingerprint."""
    store = DatasetStore(tmp_path / "filled")
    manifest = store.write("coinbase", "BONK-USD", _filled_report())

    payload = json.loads(manifest.manifest_path.read_text())
    assert payload["synthetic_no_trade_intervals"] == 2
    assert manifest.synthetic_no_trade_intervals == 2
    assert store.load_manifest(manifest.content_fingerprint).synthetic_no_trade_intervals == 2
    listed = store.list_latest_verified()
    assert [item.synthetic_no_trade_intervals for item in listed] == [2]

    liquid = tuple(_traded(offset) for offset in range(24))
    ends_at = _START + timedelta(hours=24)
    plain = DatasetStore(tmp_path / "liquid").write(
        "coinbase", "BTC-USD", analyze_range(liquid, _HOUR, _START, ends_at, ends_at)
    )
    assert "synthetic_no_trade_intervals" not in json.loads(plain.manifest_path.read_text())
    assert plain.synthetic_no_trade_intervals == 0


def test_a_stored_count_that_disagrees_with_the_rows_fails_verification(tmp_path: Path) -> None:
    """A tampered count is a manifest-coverage mismatch, never trusted."""
    store = DatasetStore(tmp_path)
    manifest = store.write("coinbase", "BONK-USD", _filled_report())
    payload = json.loads(manifest.manifest_path.read_text())
    payload["synthetic_no_trade_intervals"] = 1
    manifest.manifest_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))

    with pytest.raises(DatasetStoreError, match="manifest facts do not match"):
        DatasetStore(tmp_path).load_manifest(manifest.content_fingerprint)


def test_a_manifest_without_a_count_is_counted_from_its_rows(tmp_path: Path) -> None:
    """Manifests written before ADR 0095 carry no count; verification derives it."""
    store = DatasetStore(tmp_path)
    manifest = store.write("coinbase", "BONK-USD", _filled_report())
    payload = json.loads(manifest.manifest_path.read_text())
    del payload["synthetic_no_trade_intervals"]
    manifest.manifest_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))

    verified = DatasetStore(tmp_path).load_manifest(manifest.content_fingerprint)

    assert verified.synthetic_no_trade_intervals == 2


def test_edge_candles_come_from_the_first_and_last_partitions(tmp_path: Path) -> None:
    """The worker reads a stored edge bar when the provider omits a no-trade overlap bar."""
    store = DatasetStore(tmp_path)
    manifest = store.write("coinbase", "BONK-USD", _filled_report())

    first = store.load_edge_candle(manifest.content_fingerprint, newest=False)
    last = store.load_edge_candle(manifest.content_fingerprint, newest=True)

    assert first.starts_at == _START
    assert last.starts_at == _START + timedelta(hours=23)
    assert len(manifest.files) == 2, "the fixture spans two UTC days"


def test_no_trade_bar_is_flat_with_zero_volume() -> None:
    """The constructor itself encodes the flat zero-volume shape."""
    bar = no_trade_bar(_START, Decimal("0.00001234"))

    assert (bar.open, bar.high, bar.low, bar.close) == (Decimal("0.00001234"),) * 4
    assert bar.volume == 0
    assert is_no_trade_bar(bar) is True
