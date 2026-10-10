"""Recorded futures contract facts and hourly funding history (ADR 0126, slice P0-3).

The venue publishes only the current funding rate, so history exists only from the day
the poller runs. Live evidence (2026-10-10 00:48Z): the listing's ``funding_time`` is the
most recent funding hour (``00:00Z``), not the next one, and its ``funding_rate`` is that
hour's rate. A funding hour therefore stays *current* while the listing names it and is
**settled** once the listing names a later hour for the same contract. The settled rate is
the last value observed while the hour was current. A settled row is immutable: a later,
different value for that hour is counted as a conflict and never rewritten. Hours that were
never observed are gaps; they are reported and never filled.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date
    from decimal import Decimal

    from thytrader.market_data.instruments import FuturesProduct, InstrumentKind

FUTURES_POLL_INTERVAL_SECONDS = 300
FUTURES_PROVIDER = "coinbase"


class FuturesObservationUnavailableError(RuntimeError):
    """Signal that durable futures observation storage is disabled or unavailable."""


@dataclass(frozen=True, slots=True)
class FuturesInstrumentObservation:
    """The slowly changing facts of one contract, identified by a payload fingerprint.

    Funding rate and time are excluded (they live in the funding table), and so are the
    session open and close instants, which roll every session; the session *state* and the
    maintenance window are kept. A new row is stored only when the fingerprint changes.
    Decimal values are exact strings; ``None`` means the venue did not list the value.
    """

    product_id: str
    kind: InstrumentKind
    contract_code: str
    underlying: str
    settlement_currency: str
    contract_size: str
    price_increment: str
    base_increment: str
    base_min_size: str
    venue_expiry_at: datetime
    listed_expiry: date
    twenty_four_by_seven: bool
    intraday_long_margin_rate: str | None
    intraday_short_margin_rate: str | None
    overnight_long_margin_rate: str | None
    overnight_short_margin_rate: str | None
    funding_interval_seconds: int | None
    session_state: str | None
    maintenance_starts_at: datetime | None
    maintenance_ends_at: datetime | None
    asset_type: str | None
    trading_enabled: bool

    @property
    def payload_fingerprint(self) -> str:
        """Content identity over every field, serialized without binary floats."""
        canonical = json.dumps(asdict(self), default=str, sort_keys=True, separators=(",", ":"))
        return "sha256:" + sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class FundingSample:
    """One listed funding rate for one hour, as seen by one poll."""

    product_id: str
    funding_time: datetime
    rate: Decimal
    interval_seconds: int


@dataclass(frozen=True, slots=True)
class FundingRateRecord:
    """The stored funding history for one contract and funding hour.

    Attributes:
        rate: The settled rate once ``settled``; until then the latest observed value.
        observation_count: Polls that listed this hour.
        revision_count: Times the listed value changed while the hour was current.
        settled: True once the listing named a later hour for this contract.
        conflict_count: Later observations that disagreed with the settled rate.
    """

    product_id: str
    funding_time: datetime
    rate: Decimal
    interval_seconds: int
    first_observed_at: datetime
    last_observed_at: datetime
    observation_count: int
    revision_count: int
    settled: bool
    settled_at: datetime | None
    conflict_count: int
    last_conflict_rate: Decimal | None
    last_conflict_at: datetime | None


@dataclass(frozen=True, slots=True)
class FundingRecordOutcome:
    """What one poll changed in the funding history."""

    inserted: int
    settled: int
    conflicts: tuple[FundingSample, ...]


@dataclass(frozen=True, slots=True)
class FuturesPollState:
    """The poller's last attempt and success, so a stopped poller is visible."""

    provider: str
    last_attempt_at: datetime
    last_success_at: datetime | None
    consecutive_failures: int
    failure_code: str | None
    listing_fingerprint: str | None
    contract_count: int | None
    perpetual_count: int | None


class FuturesObservationStore(Protocol):
    """Durable futures contract observations, funding history and poll status."""

    async def record_poll(
        self,
        *,
        observed_at: datetime,
        observations: tuple[FuturesInstrumentObservation, ...],
        samples: tuple[FundingSample, ...],
        listing_fingerprint: str,
        perpetual_count: int,
    ) -> FundingRecordOutcome:
        """Store one successful poll atomically and return the funding changes."""
        ...

    async def record_poll_failure(self, *, attempted_at: datetime, failure_code: str) -> None:
        """Record a failed poll without touching any history."""
        ...

    async def poll_state(self) -> FuturesPollState | None:
        """Return the poller status, or ``None`` before the first poll."""
        ...

    async def funding_rates(
        self, *, product_id: str | None, starts_at: datetime, ends_at: datetime
    ) -> tuple[FundingRateRecord, ...]:
        """Return funding rows with ``starts_at <= funding_time < ends_at``, oldest first."""
        ...

    async def funding_history_starts(self) -> dict[str, datetime]:
        """Return each contract's first recorded funding hour."""
        ...

    async def trades_around_the_clock(self) -> dict[str, bool]:
        """Return each observed contract's latest ``twenty_four_by_seven`` flag."""
        ...


def instrument_observation(product: FuturesProduct) -> FuturesInstrumentObservation:
    """Project one parsed contract onto its recorded slowly changing facts."""
    intraday = product.intraday_margin
    overnight = product.overnight_margin
    maintenance = product.session.maintenance
    return FuturesInstrumentObservation(
        product_id=product.product_id,
        kind=product.kind,
        contract_code=product.contract_code,
        underlying=product.underlying,
        settlement_currency=product.settlement_currency,
        contract_size=str(product.contract_size),
        price_increment=str(product.price_increment),
        base_increment=str(product.base_increment),
        base_min_size=str(product.base_min_size),
        venue_expiry_at=product.venue_expiry_at,
        listed_expiry=product.listed_expiry,
        twenty_four_by_seven=product.twenty_four_by_seven,
        intraday_long_margin_rate=None if intraday is None else str(intraday.long),
        intraday_short_margin_rate=None if intraday is None else str(intraday.short),
        overnight_long_margin_rate=None if overnight is None else str(overnight.long),
        overnight_short_margin_rate=None if overnight is None else str(overnight.short),
        funding_interval_seconds=(
            None if product.funding is None else int(product.funding.interval.total_seconds())
        ),
        session_state=product.session.state,
        maintenance_starts_at=None if maintenance is None else maintenance.starts_at,
        maintenance_ends_at=None if maintenance is None else maintenance.ends_at,
        asset_type=product.asset_type,
        trading_enabled=product.trading_enabled,
    )


def funding_samples(products: Iterable[FuturesProduct]) -> tuple[FundingSample, ...]:
    """Return one sample per perp whose listing names both a rate and a funding hour.

    A perp listed without a rate or time contributes nothing: an unknown rate is never
    recorded as zero.
    """
    samples: list[FundingSample] = []
    for product in products:
        funding = product.funding
        if funding is None or funding.rate is None or funding.funding_time is None:
            continue
        samples.append(
            FundingSample(
                product_id=product.product_id,
                funding_time=funding.funding_time,
                rate=funding.rate,
                interval_seconds=int(funding.interval.total_seconds()),
            )
        )
    return tuple(sorted(samples, key=lambda sample: sample.product_id))


def funding_gaps(
    records: Iterable[FundingRateRecord],
    *,
    history_starts_at: datetime | None,
    starts_at: datetime,
    ends_at: datetime,
    interval: timedelta,
) -> tuple[datetime, ...]:
    """Return the funding hours in ``[starts_at, ends_at)`` with no stored row.

    Hours before the contract's first recorded hour are not gaps: history begins when
    polling began. ``ends_at`` should be the latest hour the listing could have named.
    """
    if history_starts_at is None or interval <= timedelta(0):
        return ()
    seen = {record.funding_time for record in records}
    first = max(history_starts_at, _ceil_to(starts_at, interval))
    gaps: list[datetime] = []
    cursor = first
    while cursor < ends_at:
        if cursor not in seen:
            gaps.append(cursor)
        cursor += interval
    return tuple(gaps)


def _ceil_to(instant: datetime, interval: timedelta) -> datetime:
    """Round a UTC instant up to the next multiple of ``interval`` since the epoch."""
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    steps = -((epoch - instant.astimezone(UTC)) // interval)
    return epoch + steps * interval
