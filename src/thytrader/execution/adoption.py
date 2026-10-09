"""Inventory adoption requests, the read-only preview, and the reads both actions share.

A live book can take ownership of coins the Coinbase account already holds (ADR 0124):
``protect`` adopts them into a discretionary book that rests a stop and take-profit;
``sell`` adopts them into a stopped book flagged FLATTEN, which the worker then sells. The
preview reports the venue balance, every live book's claims, the adoptable quantity, the
mark and what would block each action, without writing anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from enum import StrEnum
from typing import TYPE_CHECKING

from thytrader.execution.mark_context import closed_mark_context
from thytrader.market_data.models import EXECUTION_TIMEFRAMES
from thytrader.market_data.products import is_spot_product_id
from thytrader.risk.accounting_evidence import accounting_snapshot
from thytrader.trading.adoption_write import (
    ADOPTION_QUANTITY_UNAVAILABLE,
    AdoptionRefusedError,
    read_venue_balances,
)
from thytrader.trading.geometry import base_currency
from thytrader.trading.ids import utc_now
from thytrader.trading.inventory_claims import (
    ADOPTION_BASE_UNRESOLVED,
    BaseAvailability,
    base_availability,
    managed_base_claims,
)
from thytrader.trading.models import (
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    ExecutionConflictError,
    IntentOrigin,
    RuntimePhase,
)
from thytrader.trading.sizing import quantize_to_increment

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.exchanges.models import ExchangeBalance
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.trading.adoption_write import BalanceReader
    from thytrader.trading.models import Deployment, DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore

ADOPTION_LIVE_ONLY = "ADOPTION_LIVE_ONLY"
ADOPTION_BELOW_VENUE_MINIMUM = "ADOPTION_BELOW_VENUE_MINIMUM"
ADOPTION_BOOK_OCCUPIED = "ADOPTION_BOOK_OCCUPIED"
ADOPTION_MARK_UNAVAILABLE = "ADOPTION_MARK_UNAVAILABLE"
ADOPTION_BALANCE_UNAVAILABLE = "ADOPTION_BALANCE_UNAVAILABLE"
ADOPTION_INVALID_REQUEST = "ADOPTION_INVALID_REQUEST"
BALANCE_READ_TIMEOUT_SECONDS = 10.0
_OCCUPIED = {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}


class AdoptionAction(StrEnum):
    """What happens to the adopted coins."""

    PROTECT = "protect"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class AdoptionRequest:
    """One validated live adoption request; ``quantity`` None adopts every unit."""

    action: AdoptionAction
    product_id: str
    quantity: Decimal | None
    idempotency_key: str
    origin: IntentOrigin
    timeframe: str
    stop_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class AdoptionPreview:
    """What an adoption of ``product_id`` would see now; never written anywhere."""

    product_id: str
    base: str
    timeframe: str
    mark: Decimal | None
    mark_bar_starts_at: str | None
    base_increment: Decimal | None
    availability: BaseAvailability | None
    protect_blocking_reasons: tuple[str, ...]
    sell_blocking_reasons: tuple[str, ...]


def parse_adoption_request(
    *,
    mode: str,
    action: str,
    product_id: str,
    quantity: str,
    idempotency_key: str,
    origin: str,
    timeframe: str,
    stop_price: str | None = None,
    take_profit_price: str | None = None,
    note: str | None = None,
) -> AdoptionRequest:
    """Validate one request; paper is refused because it holds no venue coins.

    Raises:
        AdoptionRefusedError: Paper mode (``ADOPTION_LIVE_ONLY``) or an invalid field.
    """
    if mode != DeploymentMode.LIVE.value:
        raise AdoptionRefusedError(
            ADOPTION_LIVE_ONLY,
            "Inventory adoption is live-only: paper has no venue holdings to adopt.",
        )
    try:
        parsed_action = AdoptionAction(action)
        parsed_origin = IntentOrigin(origin)
    except ValueError as error:
        raise AdoptionRefusedError(ADOPTION_INVALID_REQUEST, str(error)) from error
    if parsed_origin is IntentOrigin.RUNTIME:
        raise AdoptionRefusedError(
            ADOPTION_INVALID_REQUEST, "Adoption requires human or agent origin."
        )
    if timeframe not in EXECUTION_TIMEFRAMES:
        raise AdoptionRefusedError(ADOPTION_INVALID_REQUEST, f"Unsupported timeframe {timeframe}.")
    if not is_spot_product_id(product_id):
        raise AdoptionRefusedError(ADOPTION_INVALID_REQUEST, "Product must be a spot product.")
    stop = _optional_price("stop_price", stop_price)
    target = _optional_price("take_profit_price", take_profit_price)
    if parsed_action is AdoptionAction.PROTECT and (stop is None or target is None):
        raise AdoptionRefusedError(
            ADOPTION_INVALID_REQUEST, "protect requires stop_price and take_profit_price."
        )
    if parsed_action is AdoptionAction.SELL and (stop is not None or target is not None):
        raise AdoptionRefusedError(
            ADOPTION_INVALID_REQUEST, "sell takes no stop_price or take_profit_price."
        )
    return AdoptionRequest(
        action=parsed_action,
        product_id=product_id,
        quantity=None if quantity.strip().lower() == "all" else _positive("quantity", quantity),
        idempotency_key=idempotency_key,
        origin=parsed_origin,
        timeframe=timeframe,
        stop_price=stop,
        take_profit_price=target,
        note=note,
    )


async def preview_adoption(
    *,
    store: ExecutionStore,
    market_data: MarketDataService,
    read_balances: BalanceReader | None,
    product_id: str,
    timeframe: str,
) -> AdoptionPreview:
    """Report balances, claims, the adoptable quantity, the mark and blocking reasons."""
    base = base_currency(product_id)
    common: list[str] = []
    product: MarketProduct | None = None
    candle: Candle | None = None
    try:
        product, candle = await closed_mark_context(
            market_data, product_id=product_id, timeframe=timeframe
        )
    except ExecutionConflictError as error:
        common.append(f"{ADOPTION_MARK_UNAVAILABLE}: {error}")
    deployments = await store.list_deployments()
    availability: BaseAvailability | None = None
    if read_balances is None:
        common.append(f"{ADOPTION_BALANCE_UNAVAILABLE}: no live Coinbase account is configured.")
    else:
        try:
            balances = await read_venue_balances(
                read_balances, timeout_seconds=BALANCE_READ_TIMEOUT_SECONDS
            )
        except AdoptionRefusedError as error:
            common.append(str(error))
        else:
            availability = base_availability(
                balances,
                managed_base_claims(await live_accounting_books(store, deployments), base),
                base_increment=None if product is None else product.base_increment,
            )
            common.extend(_availability_reasons(availability, product, candle))
    occupied = occupied_discretionary_book(deployments, product_id=product_id)
    protect = [*common]
    if occupied is not None:
        protect.append(
            f"{ADOPTION_BOOK_OCCUPIED}: discretionary book {occupied.id} already holds "
            f"or works {product_id}."
        )
    return AdoptionPreview(
        product_id=product_id,
        base=base,
        timeframe=timeframe,
        mark=None if candle is None else candle.close,
        mark_bar_starts_at=None if candle is None else candle.starts_at.isoformat(),
        base_increment=None if product is None else product.base_increment,
        availability=availability,
        protect_blocking_reasons=tuple(protect),
        sell_blocking_reasons=tuple(common),
    )


async def live_accounting_books(
    store: ExecutionStore, deployments: Sequence[Deployment]
) -> tuple[DeploymentSnapshot, ...]:
    """Every live book's complete accounting, including stopped books."""
    return tuple(
        [
            await accounting_snapshot(store, item.id, as_of=utc_now())
            for item in deployments
            if item.mode is DeploymentMode.LIVE
        ]
    )


def occupied_discretionary_book(
    deployments: Sequence[Deployment], *, product_id: str
) -> Deployment | None:
    """A running or paused live discretionary book on the product that is not flat."""
    return next(
        (
            item
            for item in deployments
            if item.kind is DeploymentKind.DISCRETIONARY
            and item.mode is DeploymentMode.LIVE
            and item.product_id == product_id
            and item.status in _OCCUPIED
            and not _flat_running(item)
        ),
        None,
    )


def reusable_discretionary_book(
    deployments: Sequence[Deployment], *, product_id: str
) -> Deployment | None:
    """A flat running live discretionary book on the product that can adopt."""
    return next(
        (
            item
            for item in deployments
            if item.kind is DeploymentKind.DISCRETIONARY
            and item.mode is DeploymentMode.LIVE
            and item.product_id == product_id
            and _flat_running(item)
        ),
        None,
    )


def adopted_quantity(
    request: AdoptionRequest, *, availability: BaseAvailability, product: MarketProduct
) -> Decimal:
    """Resolve "all" or round the request down to the base increment; refuse the impossible.

    Raises:
        AdoptionRefusedError: Unresolved claims, no unmanaged base, or more than is free.
    """
    if availability.adoptable is None:
        reasons = ", ".join(reason.value for reason in availability.reasons)
        raise AdoptionRefusedError(
            ADOPTION_BASE_UNRESOLVED,
            f"{availability.base} holdings cannot be attributed safely ({reasons}).",
        )
    adoptable = availability.adoptable
    quantity = (
        adoptable
        if request.quantity is None
        else quantize_to_increment(request.quantity, product.base_increment, rounding=ROUND_DOWN)
    )
    if quantity <= 0 or quantity > adoptable:
        raise AdoptionRefusedError(
            ADOPTION_QUANTITY_UNAVAILABLE,
            f"Only {adoptable} unmanaged {availability.base} is available to adopt.",
        )
    return quantity


def venue_minimum_refusal(
    product: MarketProduct, *, quantity: Decimal, mark: Decimal
) -> AdoptionRefusedError | None:
    """Refuse a lot the venue could not protect or sell (below base or quote minimum)."""
    if quantity < product.base_min_size or quantity * mark < product.quote_min_size:
        return AdoptionRefusedError(
            ADOPTION_BELOW_VENUE_MINIMUM,
            f"{quantity} {base_currency(product.product_id)} at {mark} is below the venue "
            f"minimum ({product.base_min_size} base, {product.quote_min_size} quote).",
        )
    return None


def _availability_reasons(
    availability: BaseAvailability, product: MarketProduct | None, candle: Candle | None
) -> list[str]:
    """Blocking reasons that come from the claims and the venue minimums."""
    if availability.adoptable is None:
        reasons = ", ".join(reason.value for reason in availability.reasons)
        return [f"{ADOPTION_BASE_UNRESOLVED}: {reasons}"]
    if availability.adoptable <= 0:
        return [f"{ADOPTION_QUANTITY_UNAVAILABLE}: no unmanaged {availability.base} is held."]
    if product is not None and candle is not None:
        refusal = venue_minimum_refusal(product, quantity=availability.adoptable, mark=candle.close)
        if refusal is not None:
            return [str(refusal)]
    return []


def _flat_running(deployment: Deployment) -> bool:
    """Running and flat: a discretionary book an adoption may reuse."""
    return deployment.status is DeploymentStatus.RUNNING and deployment.phase is RuntimePhase.FLAT


def _positive(name: str, text: str) -> Decimal:
    """Parse one positive finite decimal field."""
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise AdoptionRefusedError(
            ADOPTION_INVALID_REQUEST, f"{name} must be a decimal or 'all'."
        ) from error
    if not value.is_finite() or value <= 0:
        raise AdoptionRefusedError(ADOPTION_INVALID_REQUEST, f"{name} must be positive.")
    return value


def _optional_price(name: str, text: str | None) -> Decimal | None:
    """Parse an optional positive price."""
    return None if text is None else _positive(name, text)


def read_balances_or_refuse(
    read_balances: BalanceReader | None,
) -> BalanceReader:
    """The live account's balance reader; adoption is refused without one."""
    if read_balances is None:
        raise ExecutionConflictError("Live trading requires configured Coinbase credentials.")
    return read_balances


async def venue_rows(read_balances: BalanceReader) -> tuple[ExchangeBalance, ...]:
    """Read the venue balances once for the pre-lock quantity and admission check."""
    return await read_venue_balances(read_balances, timeout_seconds=BALANCE_READ_TIMEOUT_SECONDS)
