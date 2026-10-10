"""Provider-neutral Coinbase CFM futures account models (ADR 0127, read-only).

Every amount is an exact ``Decimal`` in USD, the CFM settlement currency. It is never added
to a USDC or USDT amount. ``None`` means the venue did not report the value: it is unknown,
never zero. ``cbi_usd_balance`` is spot-account USD that CFM can pull as margin; it is
reported as a fact here and is not added to any spot total.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

FUTURES_ACCOUNT_CURRENCY = "USD"


class FuturesEnablement(StrEnum):
    """Whether the account can use CFM futures, as far as a read can prove it.

    ``enabled`` requires a parsed balance summary. ``not_enabled`` is reserved for a
    documented venue response that proves the account has no futures access; Coinbase
    documents none today, so every failed or unparseable read is ``unknown``.
    """

    ENABLED = "enabled"
    NOT_ENABLED = "not_enabled"
    UNKNOWN = "unknown"


class FuturesPositionSide(StrEnum):
    """Venue position side; ``unknown`` is the venue's own default value."""

    LONG = "long"
    SHORT = "short"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FuturesMarginMeasure:
    """One margin window's measures from the balance summary (amounts in USD)."""

    margin_window_type: str | None
    margin_level: str | None
    initial_margin: Decimal | None
    maintenance_margin: Decimal | None
    liquidation_buffer: Decimal | None
    total_hold: Decimal | None
    futures_buying_power: Decimal | None


@dataclass(frozen=True, slots=True)
class FuturesBalanceSummary:
    """The CFM balance summary; every amount is USD.

    Futures account capital is ``cbi_usd_balance + cfm_usd_balance`` from one read; the
    margin ratio is ``available_margin / liquidation_threshold``. Both are derived by
    readers, never stored as a sum.
    """

    futures_buying_power: Decimal | None
    total_usd_balance: Decimal | None
    cbi_usd_balance: Decimal | None
    cfm_usd_balance: Decimal | None
    total_open_orders_hold_amount: Decimal | None
    unrealized_pnl: Decimal | None
    daily_realized_pnl: Decimal | None
    initial_margin: Decimal | None
    available_margin: Decimal | None
    liquidation_threshold: Decimal | None
    liquidation_buffer_amount: Decimal | None
    liquidation_buffer_percentage: Decimal | None
    total_pending_transfers_amount: Decimal | None
    funding_pnl: Decimal | None
    intraday_margin: FuturesMarginMeasure | None
    overnight_margin: FuturesMarginMeasure | None


@dataclass(frozen=True, slots=True)
class FuturesPosition:
    """One open CFM position; ``number_of_contracts`` is in contracts, prices in USD."""

    product_id: str
    side: FuturesPositionSide
    number_of_contracts: Decimal
    current_price: Decimal | None
    avg_entry_price: Decimal | None
    unrealized_pnl: Decimal | None
    daily_realized_pnl: Decimal | None
    expiration_time: datetime | None


@dataclass(frozen=True, slots=True)
class FuturesMarginWindow:
    """Which margin window is in effect and the intraday killswitch flags."""

    margin_window_type: str | None
    end_time: datetime | None
    intraday_killswitch_enabled: bool | None
    enrollment_killswitch_enabled: bool | None


@dataclass(frozen=True, slots=True)
class FuturesAccountObservation:
    """One mirror cycle: every read's result or its failure, never a guess.

    ``positions`` is ``None`` when the position read failed (unknown), ``()`` when the
    venue reported none. ``read_failures`` names each failed read as
    ``<operation>:<reason>`` with no venue text.
    """

    observed_at: datetime
    enablement: FuturesEnablement
    balance: FuturesBalanceSummary | None
    positions: tuple[FuturesPosition, ...] | None
    intraday_margin_setting: str | None
    margin_window: FuturesMarginWindow | None
    read_failures: tuple[str, ...]


class FuturesAccountReadError(RuntimeError):
    """A CFM account read failed; ``reason`` is a short redacted token."""

    def __init__(self, operation: str, reason: str) -> None:
        """Record the read and a redacted reason (``http_401``, ``transport``, ``malformed``)."""
        super().__init__(f"Coinbase futures {operation} read failed ({reason}).")
        self.operation = operation
        self.reason = reason


class FuturesAccountStoreUnavailableError(RuntimeError):
    """Durable futures account mirror storage is unavailable or holds corrupt rows."""


class FuturesAccountSnapshotStore(Protocol):
    """Durable mirror snapshots of the CFM account."""

    async def record(self, observation: FuturesAccountObservation) -> None:
        """Append one observation with its positions."""
        ...

    async def latest(self) -> FuturesAccountObservation | None:
        """Return the newest observation, or ``None`` before the first."""
        ...
