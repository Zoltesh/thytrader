"""Watched data tails do not confuse chunk success with freshness."""

from datetime import UTC, datetime, timedelta

import pytest

from thytrader.operator.data_health import data_health_report, watched_tail
from thytrader.operator.market_models import (
    DataCatalogPayload,
    DataCatalogReport,
    DatasetCoverageRow,
)
from thytrader.operator.models import STANDARD_REDACTION, ReportStatus

_NOW = datetime(2026, 10, 6, 3, 40, tzinfo=UTC)


def _row(timeframe: str = "1m", end: datetime | None = _NOW) -> DatasetCoverageRow:
    """Return an apparently successful, complete island on a watched market."""
    return DatasetCoverageRow.model_validate(
        {
            "provider": "coinbase",
            "product_id": "BTC-USDC",
            "timeframe": timeframe,
            "watched": True,
            "lookback_hours": 24,
            "worker_status": "succeeded",
            "complete": True,
            "watch_complete": True,
            "island_complete": True,
            "freshness_status": "fresh",
            "covered_starts_at": _NOW - timedelta(days=1),
            "covered_ends_at": end,
            "expected_candle_count": 100,
            "received_candle_count": 100,
            "gap_count": 0,
            "missing_intervals": 0,
            "content_fingerprint": None,
            "sparsity": "none",
        }
    )


def test_complete_island_can_have_stale_tail() -> None:
    """Ten-minute lag stays stale even when the worker's last chunk succeeded."""
    row = watched_tail(_row(end=_NOW - timedelta(minutes=10)), now=_NOW)
    assert row.tail_state == "stale"
    assert row.missing_closed_bars == 10
    assert row.lag_seconds == 600
    assert row.island_complete is True
    assert row.worker_status == "succeeded"


@pytest.mark.parametrize("timeframe", ["4h", "6h", "1d"])
def test_long_clocks_use_last_completed_close(timeframe: str) -> None:
    """A midnight close is current at 03:40 on 4h, 6h and daily clocks."""
    row = watched_tail(_row(timeframe, _NOW.replace(hour=0, minute=0)), now=_NOW)
    assert row.tail_state == "fresh"
    assert row.lag_seconds == 0


def test_settling_is_absolute_and_only_one_bar() -> None:
    """Only the immediately missing close receives the bounded publication grace."""
    now = _NOW + timedelta(seconds=30)
    assert watched_tail(_row(end=_NOW - timedelta(minutes=1)), now=now).tail_state == "settling"
    assert watched_tail(_row(end=_NOW - timedelta(minutes=2)), now=now).tail_state == "stale"
    hourly = _row("1h", _NOW.replace(hour=2, minute=0))
    assert watched_tail(hourly, now=_NOW.replace(minute=2)).tail_state == "stale"


@pytest.mark.parametrize("end", [None, _NOW + timedelta(minutes=1), _NOW - timedelta(seconds=30)])
def test_missing_future_and_unaligned_end_is_not_fresh(end: datetime | None) -> None:
    """Unknown or impossible published timestamps never become a green tail."""
    row = watched_tail(_row(end=end), now=_NOW)
    assert row.tail_state == ("missing" if end is None else "invalid")
    assert row.missing_closed_bars is None


def test_partial_catalog_and_disabled_watches() -> None:
    """Omitted series cannot be reported as a complete healthy inventory."""
    catalog = DataCatalogReport(
        application_version="test",
        generated_at=_NOW,
        overall_status=ReportStatus.HEALTHY,
        components=(),
        redaction=STANDARD_REDACTION,
        partial_result_warnings=("Watchlist read unavailable.",),
        recommended_next_action="Retry.",
        payload=DataCatalogPayload(
            datasets=(
                _row(),
                _row(end=None).model_copy(update={"watched": False}),
            )
        ),
    )
    report = data_health_report(catalog)
    assert report.overall_status is ReportStatus.DEGRADED
    assert not report.payload.inventory_complete
    assert report.payload.watched_count == 1
    assert report.payload.attention_count == 0
    assert report.partial_result_warnings == catalog.partial_result_warnings
    assert report.report_kind == "data_health"


def test_stale_report_status_and_json_round_trip() -> None:
    """The versioned HTTP report preserves its clock evidence and degraded status."""
    catalog = DataCatalogReport(
        application_version="test",
        generated_at=_NOW,
        overall_status=ReportStatus.HEALTHY,
        components=(),
        redaction=STANDARD_REDACTION,
        recommended_next_action="Ready.",
        payload=DataCatalogPayload(datasets=(_row(end=_NOW - timedelta(minutes=10)),)),
    )
    report = data_health_report(catalog)
    assert report.payload.inventory_complete
    assert report.payload.attention_count == 1
    assert report.overall_status is ReportStatus.DEGRADED
    assert type(report).model_validate_json(report.model_dump_json()) == report
