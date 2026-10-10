"""Paper futures books: the bound contract, per-cycle state, funding and liquidation math.

ADR 0129 §4. A paper futures deployment binds its contract at start and never re-reads it.
Each worker cycle loads a :class:`FuturesBookState` (the binding, the overnight margin terms
from the latest catalog observation, and whether a settled funding hour is overdue) and
binds it for the book's processing with :func:`futures_book_scope`; sizing, admission,
paper fees and the liquidation check read it with :func:`current_futures_book`. Nothing
unknown defaults to zero: a missing binding, margin or funding hour denies new entries.

:func:`futures_book_equity` is the margin basis of one book: ledger equity for paper, and the
book's ``allocated_capital`` plus ledger equity for live, whose ledger starts at cash 0
(ADR 0106, ADR 0134 §3).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.models import (
    DeploymentMode,
    FundingCashFlow,
    OrderSide,
    resolved_product_id,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from uuid import UUID

    from thytrader.evaluation.futures_spec import InstrumentContract
    from thytrader.trading.futures_sizing import FuturesMarginTerms, FuturesSide
    from thytrader.trading.models import DeploymentSnapshot

FUNDING_SETTLE_GRACE = timedelta(minutes=15)
"""How long after a funding hour's settlement (the next hour) a missing rate is overdue."""

_HOUR = timedelta(hours=1)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class FuturesBookUnavailableError(RuntimeError):
    """Futures book storage could not be read or written."""


@dataclass(frozen=True, slots=True)
class BoundFuturesContract:
    """The contract one paper futures deployment bound at start, and its per-contract fee."""

    deployment_id: UUID
    contract: InstrumentContract
    fee_per_contract: Decimal
    bound_at: datetime


class FuturesContractStore(Protocol):
    """Durable contract bindings of paper futures deployments."""

    async def bind_contract(self, binding: BoundFuturesContract) -> None:
        """Record one deployment's binding; a second binding for it is refused."""
        ...

    async def load_contract(self, deployment_id: UUID) -> BoundFuturesContract | None:
        """Return one deployment's binding, or None when it has none."""
        ...


class InMemoryFuturesContractStore:
    """Process-local bindings for tests and database-free runs."""

    def __init__(self) -> None:
        """Start with no bindings."""
        self.bindings: dict[UUID, BoundFuturesContract] = {}

    async def bind_contract(self, binding: BoundFuturesContract) -> None:
        """Record one binding; never replace an existing one."""
        if binding.deployment_id in self.bindings:
            raise FuturesBookUnavailableError("The deployment already has a bound contract.")
        self.bindings[binding.deployment_id] = binding

    async def load_contract(self, deployment_id: UUID) -> BoundFuturesContract | None:
        """Return one binding."""
        return self.bindings.get(deployment_id)


@dataclass(frozen=True, slots=True)
class FuturesBookState:
    """What one cycle of a paper futures book knows; ``None`` fields are unknown.

    ``margin`` is built from the binding and the latest observed overnight rates.
    ``funding_overdue`` is the first funding hour whose settled rate or mark is still
    missing after :data:`FUNDING_SETTLE_GRACE`; while set, new entries are denied.
    ``latest_funding_rate`` is the newest settled hourly rate of a perp (None when unknown),
    which the policy's funding-rate cap reads.
    """

    deployment_id: UUID
    product_id: str
    side: FuturesSide
    binding: BoundFuturesContract | None
    margin: FuturesMarginTerms | None
    margin_observed_at: datetime | None = None
    funding_overdue: datetime | None = None
    latest_funding_rate: Decimal | None = None

    def entry_block(self) -> tuple[str, str] | None:
        """The reason code and detail that deny a new entry, or None when known."""
        if self.binding is None:
            return (
                "FUTURES_CONTRACT_UNBOUND",
                "The paper futures book has no bound contract.",
            )
        if self.margin is None:
            return (
                "FUTURES_MARGIN_UNKNOWN",
                "The catalog lists no overnight margin rates for the contract.",
            )
        if self.funding_overdue is not None:
            hour = self.funding_overdue.isoformat().replace("+00:00", "Z")
            return (
                "FUNDING_HISTORY_MISSING",
                f"No settled funding rate or mark for {hour}; entries wait until it is applied.",
            )
        return None


_FUTURES_BOOK: ContextVar[FuturesBookState | None] = ContextVar("futures_book", default=None)


@contextmanager
def futures_book_scope(state: FuturesBookState | None) -> Iterator[None]:
    """Bind one futures book's cycle state while its deployment is processed."""
    token = _FUTURES_BOOK.set(state)
    try:
        yield
    finally:
        _FUTURES_BOOK.reset(token)


def current_futures_book(deployment_id: UUID | None = None) -> FuturesBookState | None:
    """The bound futures book state, optionally only when it belongs to ``deployment_id``."""
    state = _FUTURES_BOOK.get()
    if state is None or (deployment_id is not None and state.deployment_id != deployment_id):
        return None
    return state


def futures_book_equity(
    snapshot: DeploymentSnapshot, marks: Mapping[str, Decimal]
) -> Decimal | None:
    """The book's equity in USD at ``marks``, or None when it is unknown.

    Paper books start with their simulated cash, so equity is ledger equity. A live book's
    ledger starts at cash 0 (ADR 0106) and holds only fills, fees and funding, so its equity
    is the USD ``allocated_capital`` plus ledger equity; an unset allocation is unknown.
    An incomplete mark is unknown, never zero.
    """
    ledger = ledger_from_snapshot(snapshot, marks=marks)
    if ledger.equity is None or not ledger.mark_complete:
        return None
    deployment = snapshot.deployment
    if deployment.mode is not DeploymentMode.LIVE:
        return ledger.equity
    if deployment.allocated_capital is None:
        return None
    return deployment.allocated_capital + ledger.equity


def held_quantity_before(
    snapshot: DeploymentSnapshot, product_id: str, instant: datetime
) -> Decimal:
    """Signed base quantity held just before ``instant`` from applied fills.

    Paper fills carry their bar's start, so a fill on the bar ``[T - 1h, T)`` counts as held
    at ``T`` and an exit on the bar that starts at ``T`` does not stop ``T``'s funding: the
    same hour-at-bar-end rule as the backtest kernel (ADR 0128).
    """
    return sum(
        (delta for filled_at, delta in _signed_fills(snapshot, product_id) if filled_at < instant),
        start=Decimal(0),
    )


def funding_hours_held(
    snapshot: DeploymentSnapshot, product_id: str, *, through: datetime
) -> tuple[datetime, ...]:
    """Funding hours up to ``through`` at which the book held a position and was not charged."""
    events = _signed_fills(snapshot, product_id)
    if not events:
        return ()
    charged = {flow.funding_time for flow in snapshot.funding if flow.product_id == product_id}
    hour = _EPOCH + ((events[0][0] - _EPOCH) // _HOUR + 1) * _HOUR
    due: list[datetime] = []
    held = Decimal(0)
    index = 0
    while hour <= through:
        while index < len(events) and events[index][0] < hour:
            held += events[index][1]
            index += 1
        if held == 0 and index == len(events):
            break
        if held != 0 and hour not in charged:
            due.append(hour)
        hour += _HOUR
    return tuple(due)


def _signed_fills(snapshot: DeploymentSnapshot, product_id: str) -> list[tuple[datetime, Decimal]]:
    """Applied fills of one product as (time, signed base quantity), oldest first."""
    orders = {order.id: order for order in snapshot.orders}
    events: list[tuple[datetime, Decimal]] = []
    for fill in snapshot.fills:
        order = orders.get(fill.order_id)
        if order is None or fill.economics_applied_at is None:
            continue
        if resolved_product_id(order.product_id, snapshot.deployment) != product_id:
            continue
        signed = fill.quantity if order.side is OrderSide.BUY else -fill.quantity
        events.append((fill.filled_at, signed))
    events.sort(key=lambda event: event[0])
    return events


def funding_overdue(hour: datetime, now: datetime) -> bool:
    """A funding hour settles when the next one is listed; after the grace it is overdue."""
    return now >= hour + _HOUR + FUNDING_SETTLE_GRACE


def funding_flow(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    hour: datetime,
    mark_price: Decimal,
    rate: Decimal,
    applied_at: datetime,
) -> FundingCashFlow:
    """One funding hour's cash flow: ``-signed quantity x mark x rate`` (longs pay)."""
    signed = held_quantity_before(snapshot, product_id, hour)
    return FundingCashFlow(
        deployment_id=snapshot.deployment.id,
        product_id=product_id,
        funding_time=hour,
        signed_quantity=signed,
        mark_price=mark_price,
        rate=rate,
        amount=-signed * mark_price * rate,
        applied_at=applied_at,
    )


def liquidation_due(
    terms: FuturesMarginTerms,
    *,
    cash: Decimal,
    quantity: Decimal,
    side: FuturesSide,
    adverse_price: Decimal,
) -> bool:
    """Whether book equity at the adverse price falls below maintenance there.

    The check uses the bar's adverse extreme, so it is conservative versus the venue,
    which liquidates at mark (ADR 0128 §3).
    """
    signed = -quantity if side == "short" else quantity
    equity = cash + signed * adverse_price
    return equity < terms.maintenance(quantity, adverse_price, side)
