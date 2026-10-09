"""One inventory adoption to commit, and the checks both stores run under the base lock.

A store takes the per-base lock (ADR 0124), reads every live book's claims, then reads
the venue balance through the caller's reader, and only then calls
:func:`prepare_adoption`. Read in that order, a sell that lands in between can only make
the adoptable quantity smaller. A buy can only make it larger if its intent is written
mid-section, which the shared lock on live entry intents prevents. Everything
:func:`prepare_adoption` returns is then written in one transaction.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from thytrader.trading.adoption import AdoptionRecords, adoption_records, project_adoption
from thytrader.trading.geometry import base_currency
from thytrader.trading.inventory_claims import (
    ADOPTION_BASE_UNRESOLVED,
    BaseAvailability,
    base_availability,
    managed_base_claims,
)
from thytrader.trading.models import DeploymentSnapshot, ExecutionConflictError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID

    from thytrader.exchanges.models import ExchangeBalance
    from thytrader.trading.models import Deployment, Fill, IntentOrigin

type BalanceReader = Callable[[], Awaitable[Sequence[ExchangeBalance]]]
"""Reads the venue balances once; the store calls it under the base lock."""

ADOPTION_QUANTITY_UNAVAILABLE = "ADOPTION_QUANTITY_UNAVAILABLE"
ADOPTION_BALANCE_UNAVAILABLE = "ADOPTION_BALANCE_UNAVAILABLE"
ADOPTION_REFUSED = "ADOPTION_REFUSED"


class AdoptionRefusedError(ExecutionConflictError):
    """An adoption that must not be written; ``code`` is the stable reason code."""

    def __init__(self, code: str, detail: str) -> None:
        """Keep the code and prefix the detail with it, like other pause details."""
        self.code = code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class AdoptionWrite:
    """One adoption to commit atomically under the per-base lock.

    ``quantity`` None adopts every adoptable unit. ``new_deployment`` is inserted in the
    same transaction (strategy start with adoption); ``respect_entry_latch`` then refuses
    it while the fleet latch inhibits live entries. ``expected_revision`` guards an
    existing book against a concurrent change.
    """

    deployment_id: UUID
    product_id: str
    quantity: Decimal | None
    mark: Decimal
    mark_bar_starts_at: datetime
    now: datetime
    origin: IntentOrigin
    stop_price: Decimal
    target_price: Decimal | None
    base_increment: Decimal
    idempotency_key: str | None = None
    new_deployment: Deployment | None = None
    expected_revision: int | None = None
    respect_entry_latch: bool = False

    @property
    def base(self) -> str:
        """The venue currency this adoption takes from."""
        return base_currency(self.product_id)


@dataclass(frozen=True, slots=True)
class PreparedAdoption:
    """Records and the projected book, ready to write; nothing is written yet."""

    records: AdoptionRecords
    projected: DeploymentSnapshot
    fill: Fill
    availability: BaseAvailability


@dataclass(frozen=True, slots=True)
class AdoptionCommit:
    """The reloaded book after a committed adoption, and the figures it was checked on."""

    snapshot: DeploymentSnapshot
    records: AdoptionRecords
    availability: BaseAvailability


def prepare_adoption(
    write: AdoptionWrite,
    *,
    book: DeploymentSnapshot,
    live_books: Sequence[DeploymentSnapshot],
    balances: Sequence[ExchangeBalance],
) -> PreparedAdoption:
    """Check the base's adoptable quantity and project the adoption onto ``book``.

    Raises:
        AdoptionRefusedError: The base is unresolved, the quantity is not available or
            not a whole number of base increments, or the book refuses the adoption.
    """
    if book.deployment.id != write.deployment_id:
        raise AdoptionRefusedError(ADOPTION_REFUSED, "The adoption names another book.")
    availability = base_availability(
        balances,
        managed_base_claims(live_books, write.base),
        base_increment=write.base_increment,
    )
    if availability.adoptable is None:
        reasons = ", ".join(reason.value for reason in availability.reasons)
        raise AdoptionRefusedError(
            ADOPTION_BASE_UNRESOLVED,
            f"{write.base} holdings cannot be attributed safely ({reasons}).",
        )
    quantity = _adopted_quantity(write, availability.adoptable)
    records = adoption_records(
        deployment_id=write.deployment_id,
        product_id=write.product_id,
        quantity=quantity,
        mark=write.mark,
        mark_bar_starts_at=write.mark_bar_starts_at,
        now=write.now,
        origin=write.origin,
        idempotency_key=write.idempotency_key,
    )
    try:
        projected, fill = project_adoption(
            book, records, stop_price=write.stop_price, target_price=write.target_price
        )
    except ValueError as error:
        raise AdoptionRefusedError(ADOPTION_REFUSED, str(error)) from error
    return PreparedAdoption(
        records=records, projected=projected, fill=fill, availability=availability
    )


def _adopted_quantity(write: AdoptionWrite, adoptable: Decimal) -> Decimal:
    """Resolve "all" and refuse a quantity the venue holds unmanaged no longer."""
    quantity = adoptable if write.quantity is None else write.quantity
    increment = write.base_increment
    if quantity <= 0:
        raise AdoptionRefusedError(
            ADOPTION_QUANTITY_UNAVAILABLE, f"No unmanaged {write.base} is available to adopt."
        )
    if increment > 0 and quantity % increment != 0:
        raise AdoptionRefusedError(
            ADOPTION_QUANTITY_UNAVAILABLE,
            f"Quantity {quantity} is not a multiple of the base increment {increment}.",
        )
    if quantity > adoptable:
        raise AdoptionRefusedError(
            ADOPTION_QUANTITY_UNAVAILABLE,
            f"Only {adoptable} unmanaged {write.base} is available to adopt.",
        )
    return quantity


async def read_venue_balances(
    reader: BalanceReader, *, timeout_seconds: float
) -> tuple[ExchangeBalance, ...]:
    """Read the venue balances once; any failure or timeout refuses, never assumes zero."""
    try:
        async with asyncio.timeout(timeout_seconds):
            return tuple(await reader())
    # Any venue failure, including a timeout, means the balances are unknown.
    except Exception as error:
        raise AdoptionRefusedError(
            ADOPTION_BALANCE_UNAVAILABLE,
            f"Venue balances could not be read ({type(error).__name__}).",
        ) from error


def flat_snapshot(deployment: Deployment) -> DeploymentSnapshot:
    """A new book's empty snapshot, before its row is inserted."""
    return DeploymentSnapshot(deployment=deployment, position=None)


__all__ = [
    "ADOPTION_BALANCE_UNAVAILABLE",
    "ADOPTION_QUANTITY_UNAVAILABLE",
    "ADOPTION_REFUSED",
    "AdoptionCommit",
    "AdoptionRefusedError",
    "AdoptionWrite",
    "BalanceReader",
    "PreparedAdoption",
    "flat_snapshot",
    "prepare_adoption",
    "read_venue_balances",
]
