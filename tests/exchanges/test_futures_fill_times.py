"""Futures fill timestamp evidence is exact, explicit and UTC-representable."""

from datetime import UTC, datetime

import pytest

from tests.exchanges.test_coinbase_broker import FakeTransport
from tests.exchanges.test_coinbase_futures_broker import PRODUCT, Sizes
from tests.exchanges.test_futures_fills import FILLS, fixture_rows
from thytrader.exchanges.coinbase_futures_broker import CoinbaseFuturesBroker
from thytrader.execution.broker import BrokerError


@pytest.mark.anyio
@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-01-05T12:00:00+00:99",
        "2026-01-05T12:00:00+01:00:30",
        "0001-01-01T00:00:00+00:01",
        "9999-12-31T23:59:59-00:01",
        "2026-01-05T12:00:00.1234567Z",
        "2026-01-05T12:00:00.000000001Z",
        "2026-01-05T12:00:00-00:00",
        "2026-01-05 12:00:00Z",
        "20260105T120000Z",
        "2026-01-05T12:00:00,123Z",
        "2026-01-05T12:00:00+24:00",
        "2026-01-05T24:00:00Z",
        "2026-01-05T24:00:00.000000000Z",
        "2026-01-05T24:00:00+05:30",
        "2026-01-05T12:60:00Z",
        "2026-02-30T12:00:00Z",
        "2026-01-05T23:59:60Z",
        "2026-01-05T12:00:00",
    ],
)
async def test_invalid_fill_time_is_a_stable_domain_error(timestamp: str) -> None:
    """Malformed, unknown-offset or inexact time cannot become authoritative evidence."""
    row = fixture_rows()[0]
    row["trade_time"] = timestamp
    transport = FakeTransport(gets={FILLS: [{"fills": [row]}]})
    broker = CoinbaseFuturesBroker(transport, Sizes())
    with pytest.raises(BrokerError, match=r"^Invalid futures trade timestamp\.$"):
        await broker.list_fills(product_id=PRODUCT, order_id=row["order_id"])
    assert [call[0] for call in transport.calls] == ["GET"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2026-01-05T12:00:00Z", datetime(2026, 1, 5, 12, tzinfo=UTC)),
        ("2026-01-05T23:59:59Z", datetime(2026, 1, 5, 23, 59, 59, tzinfo=UTC)),
        ("2026-01-05T23:59:59.000000000Z", datetime(2026, 1, 5, 23, 59, 59, tzinfo=UTC)),
        ("2026-01-05T23:59:59+05:30", datetime(2026, 1, 5, 18, 29, 59, tzinfo=UTC)),
        ("2026-01-05T12:00:00+05:30", datetime(2026, 1, 5, 6, 30, tzinfo=UTC)),
        ("2026-01-05T12:00:00-03:30", datetime(2026, 1, 5, 15, 30, tzinfo=UTC)),
        ("2026-01-05T12:00:00.123456+00:00", datetime(2026, 1, 5, 12, 0, 0, 123456, UTC)),
        ("2026-01-05T12:00:00.123456000Z", datetime(2026, 1, 5, 12, 0, 0, 123456, UTC)),
        ("2026-01-05t12:00:00.1z", datetime(2026, 1, 5, 12, 0, 0, 100000, UTC)),
        ("0001-01-01T00:01:00+00:01", datetime.min.replace(tzinfo=UTC)),
        ("9999-12-31T23:58:59.999999-00:01", datetime.max.replace(tzinfo=UTC)),
    ],
)
async def test_valid_fill_time_preserves_exact_utc(timestamp: str, expected: datetime) -> None:
    """Nonzero offsets and representable fractional seconds retain the exact instant."""
    row = fixture_rows()[0]
    row["trade_time"] = timestamp
    broker = CoinbaseFuturesBroker(FakeTransport(gets={FILLS: [{"fills": [row]}]}), Sizes())
    fills = await broker.list_fills(product_id=PRODUCT, order_id=row["order_id"])
    assert fills[0].filled_at == expected
    assert fills[0].filled_at.tzinfo is UTC
