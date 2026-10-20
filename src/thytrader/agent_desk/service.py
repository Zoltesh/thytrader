"""Desk lifecycle: create, settle, brief, and apply one journaled wake (ADR 0147).

The desk funds each trade with a fresh discretionary paper book from its cash pool and settles
the book's cash back once it is flat and stopped. Every decision is checked against the desk's
caps here, whatever the model said; a refusal is journaled with its code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING, Protocol

from thytrader.agent_desk.briefing import (
    DeskBriefing,
    DeskView,
    PositionView,
    finish_briefing,
    wake_views,
)
from thytrader.agent_desk.decisions import DecisionParseError, parse_decision
from thytrader.agent_desk.market import MarketRow, market_rows
from thytrader.agent_desk.models import (
    MAX_RESPONSE_CHARS,
    Desk,
    DeskAction,
    DeskBook,
    DeskCaps,
    DeskDecision,
    DeskStatus,
    DeskWake,
    WakeOutcome,
)
from thytrader.agent_desk.score import DeskScore, score_desk
from thytrader.agent_desk.store import DeskConflictError
from thytrader.execution.discretionary_request import DiscretionaryOrderRequest
from thytrader.ids import uuid7
from thytrader.market_data.products import is_spot_product_id
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentStatus,
    ExecutionConflictError,
    IntentOrigin,
    OrderKind,
    OrderStatus,
    PositionSide,
    RuntimePhase,
)

if TYPE_CHECKING:
    from thytrader.agent_desk.store import DeskStore
    from thytrader.market_data.service import MarketDataService
    from thytrader.trading.models import DeploymentSnapshot

_ACTIVE_ORDERS = {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}
_CENT = Decimal("0.01")
_HISTORY = 50
_SCORED_WAKES = 20_000


class DeskError(RuntimeError):
    """A desk request was refused; the message leads with a code."""


class DeskExecution(Protocol):
    """The discretionary paper book operations a desk needs (wired by the API process)."""

    async def snapshot(self, deployment_id: str) -> DeploymentSnapshot:
        """Return one book."""
        ...

    async def open_book(self, request: DiscretionaryOrderRequest) -> DeploymentSnapshot:
        """Place one discretionary order on a fresh paper book."""
        ...

    async def stop_book(self, deployment_id: str, *, flatten: bool) -> DeploymentSnapshot:
        """Stop one book, flattening it when asked."""
        ...

    async def fee_rates(self) -> tuple[Decimal, Decimal] | None:
        """The account's paper maker and taker rates, or None when unreadable."""
        ...


@dataclass(frozen=True, slots=True)
class DeskSpec:
    """What a new desk is created with."""

    name: str
    quote_currency: str
    starting_cash: Decimal
    products: tuple[str, ...]
    timeframe: str = "5m"
    cadence_minutes: int = 30
    harness: str = "claude"
    model: str | None = None
    mandate: str = ""
    caps: DeskCaps = field(default_factory=DeskCaps)


@dataclass(frozen=True, slots=True)
class _State:
    """A desk after settlement, with its marked open books."""

    desk: Desk
    open_books: tuple[tuple[DeskBook, DeploymentSnapshot], ...]
    positions: tuple[PositionView, ...]
    equity: Decimal | None
    market: tuple[MarketRow, ...]
    entries_today: int


async def create_desk(store: DeskStore, spec: DeskSpec, *, now: datetime) -> Desk:
    """Validate the universe against the quote currency and insert the desk."""
    for product_id in spec.products:
        if not is_spot_product_id(product_id) or not product_id.endswith(f"-{spec.quote_currency}"):
            raise DeskError(
                f"DESK_PRODUCT_INVALID: {product_id} is not a {spec.quote_currency} spot product."
            )
    desk = Desk(
        desk_id=str(uuid7(now)),
        name=spec.name,
        quote_currency=spec.quote_currency,
        starting_cash=spec.starting_cash,
        cash=spec.starting_cash,
        products=spec.products,
        timeframe=spec.timeframe,
        cadence_minutes=spec.cadence_minutes,
        harness=spec.harness,
        model=spec.model,
        mandate=spec.mandate,
        caps=spec.caps,
        created_at=now,
        updated_at=now,
    )
    return await store.create_desk(desk)


async def briefing(
    desk_id: str,
    *,
    store: DeskStore,
    execution: DeskExecution,
    market_data: MarketDataService,
    now: datetime | None = None,
) -> DeskBriefing:
    """Settle finished books, mark open ones and assemble the briefing."""
    moment = now or datetime.now(UTC)
    state = await _state(desk_id, store=store, execution=execution, market_data=market_data)
    rates = await execution.fee_rates()
    wakes = await store.list_wakes(desk_id, limit=_HISTORY)
    return finish_briefing(
        DeskBriefing(
            generated_at=moment,
            desk=_desk_view(state, rates),
            positions=state.positions,
            recent_wakes=wake_views(wakes),
            market=state.market,
        )
    )


async def score(
    desk_id: str,
    *,
    store: DeskStore,
    execution: DeskExecution,
    market_data: MarketDataService,
    now: datetime | None = None,
) -> DeskScore:
    """Settle and mark the desk, then score it against buy-and-hold benchmarks."""
    state = await _state(desk_id, store=store, execution=execution, market_data=market_data)
    rates = await execution.fee_rates()
    return await score_desk(
        state.desk,
        equity=state.equity,
        books=await store.list_books(desk_id),
        wakes=await store.list_wakes(desk_id, limit=_SCORED_WAKES),
        rows=state.market,
        market_data=market_data,
        taker_fee_rate=None if rates is None else rates[1],
        now=now,
    )


async def record_wake(
    desk_id: str,
    *,
    store: DeskStore,
    execution: DeskExecution,
    market_data: MarketDataService,
    raw_response: str,
    harness: str,
    model: str | None,
    briefing_fingerprint: str,
    started_at: datetime,
    harness_error: str | None = None,
    now: datetime | None = None,
) -> DeskWake:
    """Parse, check and apply one answer, then journal the wake whatever happened.

    ``harness_error`` records a wake whose harness failed (timeout, crash) without an answer.
    """
    moment = now or datetime.now(UTC)
    state = await _state(desk_id, store=store, execution=execution, market_data=market_data)
    wake_id = str(uuid7(moment))
    invalid = DeskWake(
        wake_id=wake_id,
        desk_id=desk_id,
        started_at=started_at,
        finished_at=moment,
        harness=harness,
        model=model,
        briefing_fingerprint=briefing_fingerprint,
        raw_response=raw_response[:MAX_RESPONSE_CHARS],
        action=DeskAction.INVALID,
        outcome=WakeOutcome.INVALID,
        equity_quote=state.equity,
    )
    if harness_error is not None:
        wake = invalid.model_copy(
            update={
                "outcome": WakeOutcome.ERROR,
                "outcome_code": "DESK_HARNESS_FAILED",
                "detail": harness_error[:500],
            }
        )
        await store.add_wake(wake)
        return wake
    try:
        decision = parse_decision(raw_response)
    except DecisionParseError as error:
        wake = invalid.model_copy(
            update={"outcome_code": "DESK_DECISION_INVALID", "detail": str(error)}
        )
        await store.add_wake(wake)
        return wake
    decided = invalid.model_copy(
        update={
            "action": DeskAction(decision.action),
            "thesis": decision.thesis,
            "decision": decision.model_dump(mode="json", exclude_none=True),
        }
    )
    try:
        deployment_id = await _apply(
            decision, state=state, store=store, execution=execution, wake_id=wake_id, now=moment
        )
    except DeskError as error:
        code, _, detail = str(error).partition(": ")
        outcome = {"outcome": WakeOutcome.REFUSED, "outcome_code": code, "detail": detail}
    except ExecutionConflictError as error:
        outcome = {
            "outcome": WakeOutcome.REFUSED,
            "outcome_code": _conflict_code(str(error)),
            "detail": str(error)[:500],
        }
    else:
        outcome = {"outcome": WakeOutcome.APPLIED, "deployment_id": deployment_id}
    wake = decided.model_copy(update=outcome)
    await store.add_wake(wake)
    return wake


async def _state(
    desk_id: str,
    *,
    store: DeskStore,
    execution: DeskExecution,
    market_data: MarketDataService,
) -> _State:
    """Settle, mark and roll the UTC day; the saved desk is the returned one."""
    desk = await _require_desk(store, desk_id)
    desk = await _settle(desk, store=store, execution=execution)
    rows = await market_rows(market_data, desk.products)
    marks = {row.product_id: row.last_close for row in rows if row.last_close is not None}
    books = [book for book in await store.list_books(desk_id) if book.settled_at is None]
    open_books: list[tuple[DeskBook, DeploymentSnapshot]] = []
    positions: list[PositionView] = []
    equity: Decimal | None = desk.cash
    for book in books:
        snapshot = await execution.snapshot(book.deployment_id)
        open_books.append((book, snapshot))
        view = _position_view(book, snapshot, marks.get(book.product_id))
        positions.append(view)
        equity = None if equity is None or view.equity_quote is None else equity + view.equity_quote
    desk = await _roll_day(desk, store=store, equity=equity)
    today = datetime.now(UTC).date()
    entries_today = sum(
        1 for book in await store.list_books(desk_id) if book.opened_at.date() == today
    )
    return _State(desk, tuple(open_books), tuple(positions), equity, rows, entries_today)


async def _settle(desk: Desk, *, store: DeskStore, execution: DeskExecution) -> Desk:
    """Stop flat running books and return the cash of flat stopped ones to the pool."""
    returned = Decimal(0)
    for book in await store.list_books(desk.desk_id):
        if book.settled_at is not None:
            continue
        snapshot = await execution.snapshot(book.deployment_id)
        if not _flat(snapshot):
            continue
        if snapshot.deployment.status is not DeploymentStatus.STOPPED:
            snapshot = await execution.stop_book(book.deployment_id, flatten=False)
            if not _flat(snapshot) or snapshot.deployment.status is not DeploymentStatus.STOPPED:
                continue
        await store.save_book(
            book.model_copy(
                update={"settled_at": datetime.now(UTC), "settled_cash": snapshot.deployment.cash}
            )
        )
        returned += snapshot.deployment.cash
    if returned == 0:
        return desk
    return await save_desk(
        desk.model_copy(update={"cash": desk.cash + returned}), store=store, previous=desk
    )


async def _roll_day(desk: Desk, *, store: DeskStore, equity: Decimal | None) -> Desk:
    """Record the first known equity of each UTC day for the daily loss cap."""
    today = datetime.now(UTC).date()
    if equity is None or (desk.day_open_at is not None and desk.day_open_at.date() == today):
        return desk
    rolled = desk.model_copy(update={"day_open_equity": equity, "day_open_at": datetime.now(UTC)})
    return await save_desk(rolled, store=store, previous=desk)


async def _apply(
    decision: DeskDecision,
    *,
    state: _State,
    store: DeskStore,
    execution: DeskExecution,
    wake_id: str,
    now: datetime,
) -> str | None:
    """Apply one checked decision; return the book it touched."""
    if decision.action == "no_trade":
        return None
    if state.desk.status is not DeskStatus.ACTIVE:
        raise DeskError(f"DESK_NOT_ACTIVE: the desk is {state.desk.status.value}.")
    if decision.action == "close":
        if decision.deployment_id not in {book.deployment_id for book, _ in state.open_books}:
            raise DeskError("DESK_UNKNOWN_BOOK: that deployment is not an open book of this desk.")
        await execution.stop_book(decision.deployment_id, flatten=True)
        return decision.deployment_id
    allocation = _check_open(decision, state)
    request = _order_request(decision, state.desk, wake_id=wake_id, allocation=allocation)
    snapshot = await execution.open_book(request)
    deployment_id = str(snapshot.deployment.id)
    await store.add_book(
        DeskBook(
            desk_id=state.desk.desk_id,
            deployment_id=deployment_id,
            product_id=request.product_id,
            side="short" if request.side is PositionSide.SHORT else "long",
            allocated_quote=allocation,
            opened_at=now,
            wake_id=wake_id,
        )
    )
    funded = state.desk.model_copy(update={"cash": state.desk.cash - allocation})
    await save_desk(funded, store=store, previous=state.desk)
    return deployment_id


def _check_open(decision: DeskDecision, state: _State) -> Decimal:
    """Enforce the universe and caps; return the allocation in quote."""
    desk, caps = state.desk, state.desk.caps
    product_id = decision.product_id or ""
    if product_id not in desk.products:
        raise DeskError(f"DESK_PRODUCT_NOT_IN_UNIVERSE: {product_id} is not a desk product.")
    if product_id in {book.product_id for book, _ in state.open_books}:
        raise DeskError(f"DESK_PRODUCT_OCCUPIED: the desk already has a book on {product_id}.")
    if len(state.open_books) >= caps.max_open_positions:
        raise DeskError(f"DESK_MAX_POSITIONS: {caps.max_open_positions} books are open.")
    if state.entries_today >= caps.max_entries_per_day:
        raise DeskError(f"DESK_MAX_ENTRIES_TODAY: {caps.max_entries_per_day} entries today.")
    if state.equity is None:
        raise DeskError("DESK_EQUITY_UNKNOWN: an open book has no mark; no new entries.")
    block = _loss_block(desk, state.equity)
    if block is not None:
        raise DeskError(block)
    fraction = decision.size_fraction or Decimal(0)
    if fraction > caps.max_position_fraction:
        raise DeskError(
            f"DESK_POSITION_TOO_LARGE: size_fraction {fraction} exceeds "
            f"{caps.max_position_fraction}."
        )
    allocation = (state.equity * fraction).quantize(_CENT, rounding=ROUND_DOWN)
    if allocation <= 0 or allocation > desk.cash:
        raise DeskError(f"DESK_CASH_SHORT: {allocation} exceeds pool cash {desk.cash}.")
    return allocation


def _order_request(
    decision: DeskDecision, desk: Desk, *, wake_id: str, allocation: Decimal
) -> DiscretionaryOrderRequest:
    """The discretionary paper order for an open decision (fresh book, account fee rates)."""
    market = decision.entry == "market"
    header = f"[desk {desk.name} | {desk.harness}{'/' + desk.model if desk.model else ''}] "
    return DiscretionaryOrderRequest(
        mode=DeploymentMode.PAPER,
        product_id=decision.product_id or "",
        entry_kind=OrderKind.MARKETABLE if market else OrderKind.POST_ONLY_LIMIT,
        stop_price=decision.stop_price or Decimal(0),
        take_profit_price=decision.take_profit_price or Decimal(0),
        origin=IntentOrigin.AGENT,
        idempotency_key=f"desk:{desk.desk_id}:{wake_id}"[:128],
        timeframe=desk.timeframe,
        side=PositionSide.SHORT if decision.side == "short" else PositionSide.LONG,
        quantity=None,
        # Leave room for entry fees inside the book's own cash.
        quote_notional=(allocation * Decimal("0.98")).quantize(_CENT, rounding=ROUND_DOWN),
        limit_price=None if market else decision.limit_price,
        paper_starting_cash=allocation,
        paper_maker_fee_rate=None,
        paper_taker_fee_rate=None,
        note=(header + decision.thesis)[:4000],
        fresh_book=True,
    )


def _desk_view(state: _State, rates: tuple[Decimal, Decimal] | None) -> DeskView:
    """Capital, caps and remaining headroom."""
    desk = state.desk
    block = "DESK_EQUITY_UNKNOWN" if state.equity is None else _loss_block(desk, state.equity)
    if desk.status is not DeskStatus.ACTIVE:
        block = f"DESK_NOT_ACTIVE: {desk.status.value}"
    left = max(0, desk.caps.max_entries_per_day - state.entries_today)
    return DeskView(
        desk_id=desk.desk_id,
        name=desk.name,
        quote_currency=desk.quote_currency,
        mandate=desk.mandate,
        starting_cash=desk.starting_cash,
        cash=desk.cash,
        equity_quote=state.equity,
        return_since_start=(
            None
            if state.equity is None
            else (state.equity / desk.starting_cash - 1).quantize(Decimal("0.0001"))
        ),
        day_open_equity=desk.day_open_equity,
        caps=desk.caps,
        open_positions=len(state.open_books),
        entries_today=state.entries_today,
        entries_left_today=left,
        new_entries_allowed=block is None
        and left > 0
        and len(state.open_books) < desk.caps.max_open_positions,
        entry_block_reason=block,
        maker_fee_rate=None if rates is None else rates[0],
        taker_fee_rate=None if rates is None else rates[1],
    )


def _loss_block(desk: Desk, equity: Decimal) -> str | None:
    """The daily loss cap, measured from the UTC day's first known equity."""
    if desk.day_open_equity is None:
        return None
    floor = desk.day_open_equity * (1 - desk.caps.daily_loss_fraction)
    if equity < floor:
        return (
            f"DESK_DAILY_LOSS: equity {equity.quantize(_CENT)} is below today's floor "
            f"{floor.quantize(_CENT)}."
        )
    return None


def _position_view(
    book: DeskBook, snapshot: DeploymentSnapshot, mark: Decimal | None
) -> PositionView:
    """One open book, marked when a close is known."""
    deployment = snapshot.deployment
    position = snapshot.position
    equity = None
    if position is None and not snapshot.positions:
        equity = deployment.cash
    elif mark is not None:
        equity = ledger_from_snapshot(snapshot, mark_price=mark).equity
    return PositionView(
        deployment_id=book.deployment_id,
        product_id=book.product_id,
        side=book.side,
        phase=deployment.phase.value,
        allocated_quote=book.allocated_quote,
        quantity=None if position is None else position.quantity,
        entry_price=None if position is None else position.entry_price,
        stop_price=position.stop_price if position else deployment.pending_stop_price,
        take_profit_price=position.target_price if position else deployment.pending_target_price,
        mark_price=mark,
        equity_quote=equity,
        unrealized_return=(
            None
            if equity is None
            else (equity / book.allocated_quote - 1).quantize(Decimal("0.0001"))
        ),
        opened_at=book.opened_at,
    )


def _flat(snapshot: DeploymentSnapshot) -> bool:
    """No position, no working order, and a flat phase."""
    return (
        snapshot.position is None
        and not snapshot.positions
        and snapshot.deployment.phase is RuntimePhase.FLAT
        and not any(order.status in _ACTIVE_ORDERS for order in snapshot.orders)
    )


def _conflict_code(message: str) -> str:
    """A leading CODE: of an execution refusal, else a generic desk code."""
    head = message.split(":", 1)[0].strip()
    if head.isupper() and "_" in head and " " not in head:
        return head
    if "occupied discretionary book" in message:
        return "DESK_PRODUCT_OCCUPIED"
    return "DESK_ORDER_REFUSED"


async def _require_desk(store: DeskStore, desk_id: str) -> Desk:
    """Return the desk or refuse."""
    desk = await store.get_desk(desk_id)
    if desk is None:
        raise DeskError(f"DESK_NOT_FOUND: no desk {desk_id}.")
    return desk


async def save_desk(updated: Desk, *, store: DeskStore, previous: Desk) -> Desk:
    """Save under the previous revision; a lost race is a retryable conflict."""
    stamped = updated.model_copy(update={"updated_at": datetime.now(UTC)})
    try:
        return await store.save_desk(stamped, expected_revision=previous.revision)
    except DeskConflictError as error:
        raise DeskError(f"DESK_CONFLICT: {error}") from error
