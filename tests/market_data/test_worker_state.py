"""Worker-state validation regressions for durable ingestion evidence."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from thytrader.market_data.models import CandleInterval
from thytrader.market_data.worker_state import (
    MarketDataMaintenanceKind,
    MarketDataWorkerError,
    MarketDataWorkerState,
    MarketDataWorkerStatus,
)


def _valid_state() -> MarketDataWorkerState:
    """Build the smallest complete, internally consistent worker state."""
    starts_at = datetime(2026, 8, 20, 10, tzinfo=UTC)
    ends_at = starts_at + timedelta(hours=1)
    return MarketDataWorkerState(
        provider="coinbase",
        product_id="BTC-USD",
        timeframe=CandleInterval.ONE_HOUR,
        status=MarketDataWorkerStatus.SUCCEEDED,
        last_attempt_at=ends_at,
        last_success_at=ends_at,
        requested_starts_at=starts_at,
        requested_ends_at=ends_at,
        covered_starts_at=starts_at,
        covered_ends_at=ends_at,
        expected_candle_count=1,
        received_candle_count=1,
        gap_count=0,
        missing_intervals=0,
        complete=True,
        content_fingerprint=f"sha256:{'a' * 64}",
        failure_code=None,
        failure_message=None,
        consecutive_failures=0,
        updated_at=ends_at,
        maintenance_kind=MarketDataMaintenanceKind.INCREMENTAL,
    )


def test_worker_state_rejects_named_zero_offset_timezone() -> None:
    """A named zero-offset zone is not canonical UTC durable evidence."""
    valid = _valid_state()
    forged_zone = timezone(timedelta(0), "forged-zero-offset-zone")

    with pytest.raises(MarketDataWorkerError, match="timezone-aware UTC"):
        replace(
            valid,
            last_attempt_at=datetime(2026, 8, 20, 11, tzinfo=forged_zone),
            updated_at=datetime(2026, 8, 20, 11, tzinfo=forged_zone),
        )
