"""PostgreSQL repository for latest market-data ingestion worker state."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from thytrader.market_data.models import CandleInterval
from thytrader.market_data.worker_state import (
    MarketDataWorkerAttempt,
    MarketDataWorkerFailure,
    MarketDataWorkerState,
    MarketDataWorkerStatus,
    MarketDataWorkerSuccess,
)
from thytrader.persistence.schema import market_data_worker_state

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.engine import Row
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.sql.base import Executable


class PostgresMarketDataWorkerStateStore:
    """Transactional latest-state repository shared by the worker and read-only API."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind state operations to one managed async engine."""
        self._engine = engine

    async def record_attempt(self, attempt: MarketDataWorkerAttempt) -> None:
        """Upsert a running attempt while preserving prior verified coverage."""
        values = _attempt_values(attempt)
        statement = insert(market_data_worker_state).values(
            **values,
            status=MarketDataWorkerStatus.RUNNING.value,
            complete=False,
            consecutive_failures=0,
            updated_at=attempt.attempted_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["provider", "product_id", "timeframe"],
            set_={
                "status": MarketDataWorkerStatus.RUNNING.value,
                "last_attempt_at": attempt.attempted_at,
                "requested_starts_at": attempt.requested_starts_at,
                "requested_ends_at": attempt.requested_ends_at,
                "updated_at": attempt.attempted_at,
            },
        )
        await self._execute(statement)

    async def record_success(self, success: MarketDataWorkerSuccess) -> None:
        """Upsert successful publication and reset consecutive failure state."""
        attempt = success.attempt
        statement = insert(market_data_worker_state).values(
            **_attempt_values(attempt),
            status=MarketDataWorkerStatus.SUCCEEDED.value,
            last_success_at=attempt.attempted_at,
            covered_starts_at=success.covered_starts_at,
            covered_ends_at=success.covered_ends_at,
            expected_candle_count=success.expected_candle_count,
            received_candle_count=success.received_candle_count,
            gap_count=success.gap_count,
            missing_intervals=success.missing_intervals,
            complete=True,
            content_fingerprint=success.content_fingerprint,
            failure_code=None,
            failure_message=None,
            consecutive_failures=0,
            updated_at=attempt.attempted_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["provider", "product_id", "timeframe"],
            set_={
                "status": MarketDataWorkerStatus.SUCCEEDED.value,
                "last_attempt_at": attempt.attempted_at,
                "last_success_at": attempt.attempted_at,
                "requested_starts_at": attempt.requested_starts_at,
                "requested_ends_at": attempt.requested_ends_at,
                "covered_starts_at": success.covered_starts_at,
                "covered_ends_at": success.covered_ends_at,
                "expected_candle_count": success.expected_candle_count,
                "received_candle_count": success.received_candle_count,
                "gap_count": success.gap_count,
                "missing_intervals": success.missing_intervals,
                "complete": True,
                "content_fingerprint": success.content_fingerprint,
                "failure_code": None,
                "failure_message": None,
                "consecutive_failures": 0,
                "updated_at": attempt.attempted_at,
            },
        )
        await self._execute(statement)

    async def record_failure(self, failure: MarketDataWorkerFailure) -> None:
        """Upsert redacted failure details while retaining prior successful coverage."""
        attempt = failure.attempt
        statement = insert(market_data_worker_state).values(
            **_attempt_values(attempt),
            status=MarketDataWorkerStatus.FAILED.value,
            complete=False,
            failure_code=failure.code,
            failure_message=failure.message,
            consecutive_failures=1,
            updated_at=attempt.attempted_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["provider", "product_id", "timeframe"],
            set_={
                "status": MarketDataWorkerStatus.FAILED.value,
                "last_attempt_at": attempt.attempted_at,
                "requested_starts_at": attempt.requested_starts_at,
                "requested_ends_at": attempt.requested_ends_at,
                "failure_code": failure.code,
                "failure_message": failure.message,
                "consecutive_failures": market_data_worker_state.c.consecutive_failures + 1,
                "updated_at": attempt.attempted_at,
            },
        )
        await self._execute(statement)

    async def get(
        self,
        provider: str,
        product_id: str,
        timeframe: CandleInterval,
    ) -> MarketDataWorkerState | None:
        """Load the latest state for one exact ingestion target."""
        statement = select(market_data_worker_state).where(
            market_data_worker_state.c.provider == provider,
            market_data_worker_state.c.product_id == product_id,
            market_data_worker_state.c.timeframe == timeframe.value,
        )
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).first()
        return _to_state(row) if row is not None else None

    async def _execute(self, statement: Executable) -> None:
        """Commit one atomic state transition."""
        async with self._engine.begin() as connection:
            await connection.execute(statement)


def _attempt_values(attempt: MarketDataWorkerAttempt) -> dict[str, object]:
    """Map stable attempt identity and bounds to database values."""
    return {
        "provider": attempt.provider,
        "product_id": attempt.product_id,
        "timeframe": attempt.timeframe.value,
        "last_attempt_at": attempt.attempted_at,
        "requested_starts_at": attempt.requested_starts_at,
        "requested_ends_at": attempt.requested_ends_at,
    }


def _to_state(row: Row[tuple[object, ...]]) -> MarketDataWorkerState:
    """Reconstruct the typed domain state from one SQLAlchemy row."""
    values = row._mapping
    return MarketDataWorkerState(
        provider=cast("str", values["provider"]),
        product_id=cast("str", values["product_id"]),
        timeframe=CandleInterval(cast("str", values["timeframe"])),
        status=MarketDataWorkerStatus(cast("str", values["status"])),
        last_attempt_at=cast("datetime", values["last_attempt_at"]),
        last_success_at=cast("datetime | None", values["last_success_at"]),
        requested_starts_at=cast("datetime", values["requested_starts_at"]),
        requested_ends_at=cast("datetime", values["requested_ends_at"]),
        covered_starts_at=cast("datetime | None", values["covered_starts_at"]),
        covered_ends_at=cast("datetime | None", values["covered_ends_at"]),
        expected_candle_count=cast("int | None", values["expected_candle_count"]),
        received_candle_count=cast("int | None", values["received_candle_count"]),
        gap_count=cast("int | None", values["gap_count"]),
        missing_intervals=cast("int | None", values["missing_intervals"]),
        complete=cast("bool", values["complete"]),
        content_fingerprint=cast("str | None", values["content_fingerprint"]),
        failure_code=cast("str | None", values["failure_code"]),
        failure_message=cast("str | None", values["failure_message"]),
        consecutive_failures=cast("int", values["consecutive_failures"]),
        updated_at=cast("datetime", values["updated_at"]),
    )
