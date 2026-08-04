"""Unit tests for PostgreSQL market-data worker row reconstruction."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from thytrader.market_data.models import CandleInterval
from thytrader.market_data.worker_state import MarketDataWorkerError
from thytrader.persistence.postgres_market_data_worker import _to_state

if TYPE_CHECKING:
    from sqlalchemy.engine import Row


def test_postgres_worker_state_mapping_rejects_forged_status_enum() -> None:
    """Malformed persisted enum text must become the worker's controlled domain error."""
    row = cast(
        "Row[tuple[object, ...]]",
        SimpleNamespace(
            _mapping={
                "provider": "coinbase",
                "product_id": "BTC-USD",
                "timeframe": CandleInterval.ONE_HOUR.value,
                "status": "forged",
            }
        ),
    )

    with pytest.raises(MarketDataWorkerError, match="malformed persisted state"):
        _to_state(row)
