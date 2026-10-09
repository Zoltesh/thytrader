"""Managed claims on one venue base currency and the quantity a live book may adopt.

Every live book (running, paused or stopped) can claim base the venue account holds. A
book's long position owns its quantity. A working buy that opens or adds inventory will own
its unfilled remainder: a venue fill lands before it is recorded locally, so the remainder
is claimed now. A working short-entry sell will consume its unfilled remainder of unmanaged
base. An opening intent that has no order row yet claims its whole quantity, because its
order may already be at the venue. A cover buy of a short or a protective sell of a long
claims nothing extra: the short owns no base, and the long's base is already claimed.

``unmanaged = total - claims`` and ``adoptable = min(available, unmanaged)`` rounded down to
the base increment. Coinbase ``available`` already excludes base held by resting
protective sells, so those are not subtracted twice. Unknown is never zero (ADR 0124):
an UNKNOWN order, unsettled or unresolved accounting on the base, or a missing or duplicate
balance row leaves the quantity unknown, and the caller must refuse with
``ADOPTION_BASE_UNRESOLVED``. Product books are grouped by base currency, so a DOGE-USD
and a DOGE-USDC book claim the same DOGE.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from thytrader.market_data.products import is_spot_product_id
from thytrader.trading.geometry import base_currency
from thytrader.trading.models import (
    INVENTORY_OPENING_PURPOSES,
    DeploymentMode,
    IntentPurpose,
    OrderSide,
    OrderStatus,
    PositionSide,
    resolved_product_id,
    snapshot_positions,
)
from thytrader.trading.protection import book_inventory_reasons

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from uuid import UUID

    from thytrader.exchanges.models import ExchangeBalance
    from thytrader.trading.models import DeploymentSnapshot, OrderIntent

ADOPTION_BASE_UNRESOLVED = "ADOPTION_BASE_UNRESOLVED"

_ZERO = Decimal(0)
_WORKING = {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}


class BaseUnresolvedReason(StrEnum):
    """Why one base's unmanaged quantity is unknown rather than a number."""

    UNKNOWN_ORDER = "unknown_order"
    FILL_ECONOMICS_UNSETTLED = "fill_economics_unsettled"
    ACCOUNTING_UNRESOLVED = "accounting_unresolved"
    BALANCE_MISSING = "balance_missing"
    DUPLICATE_BALANCE_ROWS = "duplicate_balance_rows"


@dataclass(frozen=True, slots=True)
class ManagedBaseClaims:
    """What live books own or will own of one base currency, plus why it is unknown."""

    base: str
    managed_long: Decimal
    working_buys: Decimal
    working_short_entry_sells: Decimal
    unresolved: tuple[BaseUnresolvedReason, ...] = ()

    @property
    def claimed(self) -> Decimal:
        """Every quantity subtracted from the venue total."""
        return self.managed_long + self.working_buys + self.working_short_entry_sells


@dataclass(frozen=True, slots=True)
class BaseAvailability:
    """The venue balance of one base against its managed claims.

    ``adoptable`` and ``unmanaged`` are None when any reason leaves them unknown.
    """

    base: str
    claims: ManagedBaseClaims
    total: Decimal | None
    available: Decimal | None
    unmanaged: Decimal | None
    adoptable: Decimal | None
    reasons: tuple[BaseUnresolvedReason, ...]


def managed_base_claims(snapshots: Sequence[DeploymentSnapshot], base: str) -> ManagedBaseClaims:
    """Sum every live book's claims on ``base``; paper books own no venue inventory."""
    long = working_buys = short_sells = _ZERO
    reasons: list[BaseUnresolvedReason] = []
    for snapshot in snapshots:
        if snapshot.deployment.mode is not DeploymentMode.LIVE:
            continue
        products = _base_products(snapshot, base)
        if not products:
            continue
        reasons.extend(_book_reasons(snapshot, products))
        long += sum(
            (
                position.quantity
                for position in snapshot_positions(snapshot)
                if position.side is PositionSide.LONG
                and resolved_product_id(position.product_id, snapshot.deployment) in products
            ),
            start=_ZERO,
        )
        buys, sells = _working_claims(snapshot, products)
        working_buys += buys
        short_sells += sells
    return ManagedBaseClaims(
        base=base,
        managed_long=long,
        working_buys=working_buys,
        working_short_entry_sells=short_sells,
        unresolved=tuple(dict.fromkeys(reasons)),
    )


def base_availability(
    balances: Sequence[ExchangeBalance],
    claims: ManagedBaseClaims,
    *,
    base_increment: Decimal | None = None,
) -> BaseAvailability:
    """Compare one venue balance row with the claims; round down to ``base_increment``.

    Exactly one balance row must name the base. The adoptable quantity is never negative.
    """
    rows = tuple(balance for balance in balances if balance.currency == claims.base)
    reasons = list(claims.unresolved)
    if not rows:
        reasons.append(BaseUnresolvedReason.BALANCE_MISSING)
    elif len(rows) > 1:
        reasons.append(BaseUnresolvedReason.DUPLICATE_BALANCE_ROWS)
    row = rows[0] if len(rows) == 1 else None
    total = None if row is None else row.total
    available = None if row is None else row.available
    if reasons or total is None or available is None:
        return BaseAvailability(
            base=claims.base,
            claims=claims,
            total=total,
            available=available,
            unmanaged=None,
            adoptable=None,
            reasons=tuple(dict.fromkeys(reasons)),
        )
    unmanaged = total - claims.claimed
    adoptable = max(_ZERO, min(available, unmanaged))
    if base_increment is not None and base_increment > 0:
        adoptable = (adoptable / base_increment).to_integral_value(ROUND_DOWN) * base_increment
    return BaseAvailability(
        base=claims.base,
        claims=claims,
        total=total,
        available=available,
        unmanaged=unmanaged,
        adoptable=adoptable,
        reasons=(),
    )


def unmanaged_available_base(
    balances: Sequence[ExchangeBalance], snapshots: Sequence[DeploymentSnapshot], base: str
) -> Decimal | None:
    """Base a live short may sell: available base nobody manages, or None when unknown.

    Raw ``available`` includes base that a managed long owns but has not yet protected;
    selling it short would take another book's inventory (ADR 0124).
    """
    return base_availability(balances, managed_base_claims(snapshots, base)).adoptable


def _base_products(snapshot: DeploymentSnapshot, base: str) -> frozenset[str]:
    """Spot products of one book whose base currency is ``base``."""
    deployment = snapshot.deployment
    products = {
        deployment.product_id,
        *(resolved_product_id(row.product_id, deployment) for row in snapshot.orders),
        *(resolved_product_id(row.product_id, deployment) for row in snapshot.intents),
        *(resolved_product_id(row.product_id, deployment) for row in snapshot_positions(snapshot)),
        *(row.product_id for row in snapshot.instrument_runtimes),
    }
    return frozenset(
        product
        for product in products
        if is_spot_product_id(product) and base_currency(product) == base
    )


def _book_reasons(
    snapshot: DeploymentSnapshot, products: frozenset[str]
) -> Iterable[BaseUnresolvedReason]:
    """Unknown evidence on the base in one book."""
    if not snapshot.accounting_complete:
        yield BaseUnresolvedReason.ACCOUNTING_UNRESOLVED
    for product in sorted(products):
        found = book_inventory_reasons(snapshot, product_id=product)
        if "fill_economics_unsettled" in found:
            yield BaseUnresolvedReason.FILL_ECONOMICS_UNSETTLED
        if any(reason != "fill_economics_unsettled" for reason in found):
            yield BaseUnresolvedReason.ACCOUNTING_UNRESOLVED
    if any(
        order.status is OrderStatus.UNKNOWN
        and resolved_product_id(order.product_id, snapshot.deployment) in products
        for order in snapshot.orders
    ):
        yield BaseUnresolvedReason.UNKNOWN_ORDER


def _working_claims(
    snapshot: DeploymentSnapshot, products: frozenset[str]
) -> tuple[Decimal, Decimal]:
    """Unfilled opening buys and short-entry sells, including intents with no order yet."""
    deployment = snapshot.deployment
    purposes = {intent.id: intent.purpose for intent in snapshot.intents}
    buys = sells = _ZERO
    for order in snapshot.orders:
        if (
            order.status not in _WORKING
            or resolved_product_id(order.product_id, deployment) not in products
        ):
            continue
        remaining = order.quantity - order.filled_quantity
        if remaining <= 0:
            continue
        # Missing intent evidence is conservatively an opening order.
        purpose = purposes.get(order.intent_id, IntentPurpose.ENTRY)
        if purpose not in INVENTORY_OPENING_PURPOSES:
            continue
        if order.side is OrderSide.BUY:
            buys += remaining
        else:
            sells += remaining
    ordered = {order.intent_id for order in snapshot.orders}
    for intent in _unordered_openings(snapshot.intents, ordered):
        if resolved_product_id(intent.product_id, deployment) not in products:
            continue
        if intent.side is OrderSide.BUY:
            buys += intent.quantity
        else:
            sells += intent.quantity
    return buys, sells


def _unordered_openings(
    intents: Sequence[OrderIntent], ordered: set[UUID]
) -> tuple[OrderIntent, ...]:
    """Entry intents whose order row is not written yet.

    ``submit_intent`` writes the order row before calling the broker, so an entry intent
    without one is either mid-submit or an abandoned crash. Both are claimed: the former
    may be at the venue in a moment. Adoption intents are always FILLED with their order.
    """
    return tuple(
        intent
        for intent in intents
        if intent.purpose is IntentPurpose.ENTRY
        and intent.id not in ordered
        and intent.status in _WORKING
    )
