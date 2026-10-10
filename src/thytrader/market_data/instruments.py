"""Provider-neutral instrument model: spot products and Coinbase CFM futures contracts.

ADR 0126. A spot ``MarketProduct`` keeps its existing model and catalog fingerprint; a
futures contract is a separate ``FuturesProduct`` and the two meet only in ``Instrument``
and ``InstrumentCatalog``. Futures are read-only in P0: nothing here is an order path.

Units:
- ``base_increment`` and ``base_min_size`` on a futures product are **contracts**.
- ``contract_size`` is the underlying quantity per contract (0.01 BTC for ``BIP``).
- Every futures price, rate and size is an exact ``Decimal``; an unknown venue value is
  ``None``, never zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import date, datetime, timedelta
    from decimal import Decimal

    from thytrader.market_data.models import MarketProduct


class InstrumentKind(StrEnum):
    """What one product id trades."""

    SPOT = "spot"
    DATED_FUTURE = "dated_future"
    PERPETUAL_FUTURE = "perpetual_future"


@dataclass(frozen=True, slots=True)
class MarginRates:
    """Venue initial-margin fractions for one side pair (0.21 means 21% of notional)."""

    long: Decimal
    short: Decimal


@dataclass(frozen=True, slots=True)
class MaintenanceWindow:
    """One scheduled venue maintenance window, half-open ``[starts_at, ends_at)`` in UTC."""

    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class FuturesSession:
    """The venue's current trading-session facts for one contract.

    ``state`` is the venue token (``FCM_TRADING_SESSION_STATE_OPEN``). ``None`` fields
    were absent from the listing and are unknown.
    """

    is_open: bool | None
    state: str | None
    opens_at: datetime | None
    closes_at: datetime | None
    maintenance: MaintenanceWindow | None


@dataclass(frozen=True, slots=True)
class FundingObservation:
    """The current funding rate and its next funding instant, as listed.

    Perp-style contracts only. ``rate`` is per ``interval`` (hourly on CFM); longs pay
    when it is positive. The listing carries no history: each observation is a point
    sample that the funding poller persists (ADR 0126).
    """

    interval: timedelta
    rate: Decimal | None
    funding_time: datetime | None


@dataclass(frozen=True, slots=True)
class FuturesProduct:
    """One Coinbase CFM futures contract with exact venue constraints.

    Attributes:
        product_id: Venue id, for example ``BIP-20DEC30-CDE``.
        kind: Dated or perpetual-style. Perps are detected by a funding interval, not by
            ``contract_expiry_type`` (the venue reports ``EXPIRING`` for both).
        contract_code: Venue code (``BIP``); not the underlying.
        underlying: ``contract_root_unit`` (``BTC``; ``CDEUS5`` for an index).
        settlement_currency: Always ``USD`` on CFM.
        contract_size: Underlying quantity per contract.
        price_increment: Price tick in USD.
        base_increment: Order size step in contracts.
        base_min_size: Minimum order size in contracts.
        venue_expiry_at: ``contract_expiry`` as reported. Perps report a far-future
            sentinel (``2089-12-30``), so read ``expires_at`` instead.
        listed_expiry: The day encoded in the id.
        twenty_four_by_seven: Whether the venue lists the contract as trading 24/7.
        intraday_margin: Intraday initial-margin rates, or ``None`` when not listed.
        overnight_margin: Overnight initial-margin rates, or ``None`` when not listed.
        funding: Current funding facts for perps; ``None`` for dated contracts.
        session: Current session and maintenance facts.
        asset_type: Venue asset class token (``FUTURES_ASSET_TYPE_CRYPTO``), if listed.
        display_name: Venue display name, if listed.
        trading_enabled: False when the venue disables trading or the listing.
    """

    product_id: str
    kind: InstrumentKind
    contract_code: str
    underlying: str
    settlement_currency: str
    contract_size: Decimal
    price_increment: Decimal
    base_increment: Decimal
    base_min_size: Decimal
    venue_expiry_at: datetime
    listed_expiry: date
    twenty_four_by_seven: bool
    intraday_margin: MarginRates | None
    overnight_margin: MarginRates | None
    funding: FundingObservation | None
    session: FuturesSession
    asset_type: str | None
    display_name: str | None
    trading_enabled: bool

    @property
    def expires_at(self) -> datetime | None:
        """Return the venue expiry for a dated contract; ``None`` for a perp."""
        if self.kind is InstrumentKind.PERPETUAL_FUTURE:
            return None
        return self.venue_expiry_at


@dataclass(frozen=True, slots=True)
class Instrument:
    """One spot product or futures contract under a single product id.

    Exactly one of ``spot`` and ``future`` is set, matching ``kind``.
    """

    product_id: str
    kind: InstrumentKind
    spot: MarketProduct | None = None
    future: FuturesProduct | None = None

    @property
    def trading_enabled(self) -> bool:
        """Whether the venue currently lists this instrument as enabled."""
        if self.spot is not None:
            return self.spot.trading_enabled
        if self.future is not None:
            return self.future.trading_enabled
        return False


@dataclass(frozen=True, slots=True)
class FuturesCatalogSnapshot:
    """One complete futures listing observation with its own fingerprint.

    The fingerprint is separate from the spot catalog fingerprint, which never changes
    because futures exist (ADR 0126).
    """

    products: tuple[FuturesProduct, ...]
    observed_at: datetime
    fingerprint: str


@dataclass(frozen=True, slots=True)
class InstrumentCatalog:
    """Spot and futures instruments from one read, each keeping its own fingerprint.

    ``futures_fingerprint`` is ``None`` when no futures provider is configured (demo
    mode); the catalog then holds spot instruments only.
    """

    instruments: tuple[Instrument, ...]
    spot_fingerprint: str
    futures_fingerprint: str | None
    observed_at: datetime


class FuturesCatalogProvider(Protocol):
    """Read-only futures listing capability (Coinbase CFM in production)."""

    async def list_futures_products(self) -> tuple[FuturesProduct, ...]:
        """Return the complete current futures listing or raise; never a partial list."""
        ...
