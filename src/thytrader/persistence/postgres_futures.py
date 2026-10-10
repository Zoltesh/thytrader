"""PostgreSQL store for futures contract observations and funding history (ADR 0126)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.market_data.futures_observations import (
    FUTURES_PROVIDER,
    FundingRateRecord,
    FundingRecordOutcome,
    FundingSample,
    FuturesInstrumentObservation,
    FuturesObservationUnavailableError,
    FuturesPollState,
)
from thytrader.persistence.schema import (
    futures_catalog_poll_state,
    futures_funding_rates,
    futures_instrument_observations,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.engine import Row
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

_UNAVAILABLE = "Futures observation storage is unavailable."


class PostgresFuturesObservationStore:
    """Transactional futures observation repository; one poll is one transaction."""

    def __init__(self, engine: AsyncEngine, *, provider: str = FUTURES_PROVIDER) -> None:
        """Bind the store to one async engine and provider label."""
        self._engine = engine
        self._provider = provider

    async def record_poll(
        self,
        *,
        observed_at: datetime,
        observations: tuple[FuturesInstrumentObservation, ...],
        samples: tuple[FundingSample, ...],
        listing_fingerprint: str,
        perpetual_count: int,
    ) -> FundingRecordOutcome:
        """Store contract facts, funding samples and poll success in one transaction."""
        try:
            async with self._engine.begin() as connection:
                await _record_observations(connection, observations, observed_at)
                inserted = 0
                settled = 0
                conflicts: list[FundingSample] = []
                for sample in samples:
                    settled += await _settle_older(connection, sample, observed_at)
                    outcome = await _record_sample(connection, sample, observed_at)
                    inserted += outcome == "inserted"
                    if outcome == "conflict":
                        conflicts.append(sample)
                await self._upsert_poll_state(
                    connection,
                    attempted_at=observed_at,
                    success=True,
                    failure_code=None,
                    listing_fingerprint=listing_fingerprint,
                    contract_count=len(observations),
                    perpetual_count=perpetual_count,
                )
        except SQLAlchemyError as error:
            raise FuturesObservationUnavailableError(_UNAVAILABLE) from error
        return FundingRecordOutcome(inserted=inserted, settled=settled, conflicts=tuple(conflicts))

    async def record_poll_failure(self, *, attempted_at: datetime, failure_code: str) -> None:
        """Count one failed poll; history and the last success stay untouched."""
        try:
            async with self._engine.begin() as connection:
                await self._upsert_poll_state(
                    connection,
                    attempted_at=attempted_at,
                    success=False,
                    failure_code=failure_code,
                    listing_fingerprint=None,
                    contract_count=None,
                    perpetual_count=None,
                )
        except SQLAlchemyError as error:
            raise FuturesObservationUnavailableError(_UNAVAILABLE) from error

    async def poll_state(self) -> FuturesPollState | None:
        """Return the poller status row, or ``None`` before the first poll."""
        table = futures_catalog_poll_state
        statement = select(table).where(table.c.provider == self._provider)
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).one_or_none()
        except SQLAlchemyError as error:
            raise FuturesObservationUnavailableError(_UNAVAILABLE) from error
        if row is None:
            return None
        return FuturesPollState(
            provider=row.provider,
            last_attempt_at=row.last_attempt_at,
            last_success_at=row.last_success_at,
            consecutive_failures=row.consecutive_failures,
            failure_code=row.failure_code,
            listing_fingerprint=row.listing_fingerprint,
            contract_count=row.contract_count,
            perpetual_count=row.perpetual_count,
        )

    async def funding_rates(
        self, *, product_id: str | None, starts_at: datetime, ends_at: datetime
    ) -> tuple[FundingRateRecord, ...]:
        """Return funding rows in ``[starts_at, ends_at)``, ordered by contract and hour."""
        table = futures_funding_rates
        condition = and_(table.c.funding_time >= starts_at, table.c.funding_time < ends_at)
        if product_id is not None:
            condition = and_(condition, table.c.product_id == product_id)
        statement = (
            select(table).where(condition).order_by(table.c.product_id, table.c.funding_time)
        )
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).all()
        except SQLAlchemyError as error:
            raise FuturesObservationUnavailableError(_UNAVAILABLE) from error
        return tuple(_funding_record(row) for row in rows)

    async def funding_history_starts(self) -> dict[str, datetime]:
        """Return each contract's first recorded funding hour."""
        table = futures_funding_rates
        statement = select(table.c.product_id, func.min(table.c.funding_time)).group_by(
            table.c.product_id
        )
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).all()
        except SQLAlchemyError as error:
            raise FuturesObservationUnavailableError(_UNAVAILABLE) from error
        return {str(row[0]): row[1] for row in rows}

    async def trades_around_the_clock(self) -> dict[str, bool]:
        """Return each observed contract's latest ``twenty_four_by_seven`` flag."""
        table = futures_instrument_observations
        statement = (
            select(table.c.product_id, table.c.twenty_four_by_seven)
            .distinct(table.c.product_id)
            .order_by(table.c.product_id, table.c.first_seen_at.desc())
        )
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).all()
        except SQLAlchemyError as error:
            raise FuturesObservationUnavailableError(_UNAVAILABLE) from error
        return {str(row[0]): bool(row[1]) for row in rows}

    async def _upsert_poll_state(
        self,
        connection: AsyncConnection,
        *,
        attempted_at: datetime,
        success: bool,
        failure_code: str | None,
        listing_fingerprint: str | None,
        contract_count: int | None,
        perpetual_count: int | None,
    ) -> None:
        """Insert or update the single poll-state row for this provider."""
        table = futures_catalog_poll_state
        values: dict[str, object] = {
            "provider": self._provider,
            "last_attempt_at": attempted_at,
            "consecutive_failures": 0 if success else 1,
            "failure_code": failure_code,
            "updated_at": attempted_at,
        }
        updates: dict[str, object] = {
            "last_attempt_at": attempted_at,
            "failure_code": failure_code,
            "updated_at": attempted_at,
        }
        if success:
            success_values = {
                "last_success_at": attempted_at,
                "listing_fingerprint": listing_fingerprint,
                "contract_count": contract_count,
                "perpetual_count": perpetual_count,
            }
            values.update(success_values)
            updates.update(success_values)
            updates["consecutive_failures"] = 0
        else:
            updates["consecutive_failures"] = table.c.consecutive_failures + 1
        statement = insert(table).values(**values)
        statement = statement.on_conflict_do_update(index_elements=["provider"], set_=updates)
        await connection.execute(statement)


async def _record_observations(
    connection: AsyncConnection,
    observations: tuple[FuturesInstrumentObservation, ...],
    observed_at: datetime,
) -> None:
    """Extend the current row when a contract's facts are unchanged, else add a row."""
    if not observations:
        return
    table = futures_instrument_observations
    product_ids = [observation.product_id for observation in observations]
    latest = (
        select(table.c.product_id, table.c.first_seen_at, table.c.payload_fingerprint)
        .where(table.c.product_id.in_(product_ids))
        .distinct(table.c.product_id)
        .order_by(table.c.product_id, table.c.first_seen_at.desc())
    )
    current = {
        row.product_id: (row.first_seen_at, row.payload_fingerprint)
        for row in (await connection.execute(latest)).all()
    }
    for observation in observations:
        fingerprint = observation.payload_fingerprint
        found = current.get(observation.product_id)
        if found is not None and found[1] == fingerprint:
            await connection.execute(
                update(table)
                .where(
                    table.c.product_id == observation.product_id,
                    table.c.first_seen_at == found[0],
                )
                .values(last_seen_at=observed_at)
            )
            continue
        await connection.execute(
            insert(table)
            .values(
                **_observation_values(observation),
                first_seen_at=observed_at,
                last_seen_at=observed_at,
                payload_fingerprint=fingerprint,
            )
            .on_conflict_do_nothing(index_elements=["product_id", "first_seen_at"])
        )


def _observation_values(observation: FuturesInstrumentObservation) -> dict[str, object]:
    """Map one observation onto its table columns (fingerprint and seen times excluded)."""
    return {
        "product_id": observation.product_id,
        "kind": observation.kind.value,
        "contract_code": observation.contract_code,
        "underlying": observation.underlying,
        "settlement_currency": observation.settlement_currency,
        "contract_size": observation.contract_size,
        "price_increment": observation.price_increment,
        "base_increment": observation.base_increment,
        "base_min_size": observation.base_min_size,
        "venue_expiry_at": observation.venue_expiry_at,
        "listed_expiry": observation.listed_expiry,
        "twenty_four_by_seven": observation.twenty_four_by_seven,
        "intraday_long_margin_rate": observation.intraday_long_margin_rate,
        "intraday_short_margin_rate": observation.intraday_short_margin_rate,
        "overnight_long_margin_rate": observation.overnight_long_margin_rate,
        "overnight_short_margin_rate": observation.overnight_short_margin_rate,
        "funding_interval_seconds": observation.funding_interval_seconds,
        "session_state": observation.session_state,
        "maintenance_starts_at": observation.maintenance_starts_at,
        "maintenance_ends_at": observation.maintenance_ends_at,
        "asset_type": observation.asset_type,
        "trading_enabled": observation.trading_enabled,
    }


async def _settle_older(
    connection: AsyncConnection, sample: FundingSample, observed_at: datetime
) -> int:
    """Settle every earlier current hour of this contract: the listing has moved on."""
    table = futures_funding_rates
    result = await connection.execute(
        update(table)
        .where(
            table.c.product_id == sample.product_id,
            table.c.funding_time < sample.funding_time,
            table.c.settled.is_(False),
        )
        .values(settled=True, settled_at=observed_at)
    )
    return int(result.rowcount or 0)


async def _record_sample(
    connection: AsyncConnection, sample: FundingSample, observed_at: datetime
) -> str:
    """Insert, revise, confirm or flag one sample; a settled rate is never rewritten.

    Returns ``inserted``, ``revised``, ``confirmed`` or ``conflict``.
    """
    table = futures_funding_rates
    key = (table.c.product_id == sample.product_id, table.c.funding_time == sample.funding_time)
    row = (await connection.execute(select(table).where(*key).with_for_update())).one_or_none()
    if row is None:
        await connection.execute(
            insert(table).values(
                product_id=sample.product_id,
                funding_time=sample.funding_time,
                rate=str(sample.rate),
                interval_seconds=sample.interval_seconds,
                first_observed_at=observed_at,
                last_observed_at=observed_at,
                observation_count=1,
            )
        )
        return "inserted"
    same_rate = _decimal(row.rate) == sample.rate
    if row.settled:
        if same_rate:
            return "confirmed"
        await connection.execute(
            update(table)
            .where(*key)
            .values(
                conflict_count=table.c.conflict_count + 1,
                last_conflict_rate=str(sample.rate),
                last_conflict_at=observed_at,
            )
        )
        return "conflict"
    values: dict[str, object] = {
        "last_observed_at": observed_at,
        "observation_count": table.c.observation_count + 1,
    }
    if not same_rate:
        values["rate"] = str(sample.rate)
        values["revision_count"] = table.c.revision_count + 1
    await connection.execute(update(table).where(*key).values(**values))
    return "confirmed" if same_rate else "revised"


def _funding_record(row: Row[tuple[object, ...]]) -> FundingRateRecord:
    """Map one stored funding row onto the domain record."""
    return FundingRateRecord(
        product_id=str(row.product_id),
        funding_time=row.funding_time,
        rate=_decimal(row.rate),
        interval_seconds=int(row.interval_seconds),
        first_observed_at=row.first_observed_at,
        last_observed_at=row.last_observed_at,
        observation_count=int(row.observation_count),
        revision_count=int(row.revision_count),
        settled=bool(row.settled),
        settled_at=row.settled_at,
        conflict_count=int(row.conflict_count),
        last_conflict_rate=None
        if row.last_conflict_rate is None
        else _decimal(row.last_conflict_rate),
        last_conflict_at=row.last_conflict_at,
    )


def _decimal(raw: object) -> Decimal:
    """Parse one stored exact decimal string; corruption fails closed."""
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as error:
        raise FuturesObservationUnavailableError("A stored funding rate is malformed.") from error
    if not value.is_finite():
        raise FuturesObservationUnavailableError("A stored funding rate is malformed.")
    return value
