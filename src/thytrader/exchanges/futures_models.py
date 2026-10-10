"""Provider-neutral Coinbase CFM futures account models (ADR 0127, read-only).

Every amount is an exact ``Decimal`` in USD, the CFM settlement currency. It is never added
to a USDC or USDT amount. ``None`` means the venue did not report the value: it is unknown,
never zero. ``cbi_usd_balance`` is spot-account USD that CFM can pull as margin; it is
reported as a fact here and is not added to any spot total.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from thytrader.exchanges.models import ExchangeBalance

FUTURES_ACCOUNT_CURRENCY = "USD"
# ADR 0127 §8: a futures-enabled account with no CFM USD balance reports futures buying power
# equal to its spot USDC balance, so Coinbase counts the USDC spot balance as CFM collateral.
# Futures margin and USDC spot books draw on one collateral pool.
SHARED_COLLATERAL_NOTE = (
    "Coinbase counts the USDC spot balance as CFM futures collateral (observed 2026-10-10): "
    "futures buying power is shared with USDC spot capital, not additional money. Amounts "
    "here are USD and are never added to USDC amounts."
)
_RATIO_PLACES = Decimal("0.0001")
_USDC = "USDC"


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
class SpotCollateralBalances:
    """Spot USDC and USD balances read in the same mirror cycle as the CFM account.

    USDC is CFM collateral (ADR 0127 §8), so its available and held amounts sit beside the
    futures figures to show how a futures trade draws on it. Each currency is its own
    figure: USDC is never added to USD. A currency the complete account listing did not
    report has a zero balance (the listing omits empty accounts).
    """

    usdc_available: Decimal
    usdc_hold: Decimal
    usd_available: Decimal
    usd_hold: Decimal


def spot_collateral_from(balances: Iterable[ExchangeBalance]) -> SpotCollateralBalances:
    """Collect the USDC and USD rows of one complete spot account listing.

    Rows of the same currency (one per portfolio account) are added within that currency
    only; every other currency is ignored.
    """
    totals = {currency: [Decimal(0), Decimal(0)] for currency in (_USDC, FUTURES_ACCOUNT_CURRENCY)}
    for balance in balances:
        pair = totals.get(balance.currency)
        if pair is not None:
            pair[0] += balance.available
            pair[1] += balance.hold
    return SpotCollateralBalances(
        usdc_available=totals[_USDC][0],
        usdc_hold=totals[_USDC][1],
        usd_available=totals[FUTURES_ACCOUNT_CURRENCY][0],
        usd_hold=totals[FUTURES_ACCOUNT_CURRENCY][1],
    )


@dataclass(frozen=True, slots=True)
class FuturesAccountObservation:
    """One mirror cycle: every read's result or its failure, never a guess.

    ``positions`` is ``None`` when the position read failed (unknown), ``()`` when the
    venue reported none. ``read_failures`` names each failed read as
    ``<operation>:<reason>`` with no venue text. ``spot_balances`` is ``None`` when the
    spot account read failed or was not attempted (unknown, never zero).
    """

    observed_at: datetime
    enablement: FuturesEnablement
    balance: FuturesBalanceSummary | None
    positions: tuple[FuturesPosition, ...] | None
    intraday_margin_setting: str | None
    margin_window: FuturesMarginWindow | None
    read_failures: tuple[str, ...]
    spot_balances: SpotCollateralBalances | None = None


class FuturesAccountReadError(RuntimeError):
    """A CFM account read failed; ``reason`` is a short redacted token."""

    def __init__(self, operation: str, reason: str) -> None:
        """Record the read and a redacted reason (``http_401``, ``transport``, ``malformed``)."""
        super().__init__(f"Coinbase futures {operation} read failed ({reason}).")
        self.operation = operation
        self.reason = reason


def margin_ratio(balance: FuturesBalanceSummary) -> Decimal | None:
    """Return ``available_margin / liquidation_threshold`` to four places, when defined.

    ``None`` when either amount is unknown or the threshold is not positive (a flat
    account has no liquidation threshold); never a guessed ratio.
    """
    available = balance.available_margin
    threshold = balance.liquidation_threshold
    if available is None or threshold is None or threshold <= 0:
        return None
    return (available / threshold).quantize(_RATIO_PLACES)


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


class FuturesAccountHistoryStore(Protocol):
    """Read mirror snapshots over a time window (the supervised-trade history)."""

    async def history(
        self, *, since: datetime, until: datetime, limit: int
    ) -> tuple[FuturesAccountObservation, ...]:
        """Return up to ``limit`` observations in ``[since, until)``, oldest first."""
        ...
