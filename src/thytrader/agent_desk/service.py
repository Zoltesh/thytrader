"""Desk lifecycle: create, settle, brief, and apply one journaled wake (ADR 0147).

The desk funds each trade with a fresh discretionary paper book from its cash pool and settles
the book's cash back once it is flat and stopped. Every decision is checked against the desk's
caps here, whatever the model said; a refusal is journaled with its code.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING, Literal

from thytrader.agent_desk.briefing import (
    DeskBriefing,
    DeskView,
    PositionView,
    finish_briefing,
    wake_views,
)
from thytrader.agent_desk.decisions import DecisionParseError, parse_decision
from thytrader.agent_desk.funding import open_funded, resolve_pending
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
from thytrader.agent_desk.ports import DeskError, DeskExecution, save_desk
from thytrader.agent_desk.score import DeskScore, score_desk
from thytrader.agent_desk.valuation import book_value
from thytrader.execution.discretionary_request import DiscretionaryOrderRequest
from thytrader.ids import uuid7
from thytrader.market_data.models import EXECUTION_TIMEFRAMES
from thytrader.market_data.products import is_spot_product_id
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
_LOCKS: dict[str, asyncio.Lock] = {}


def desk_lock(desk_id: str) -> asyncio.Lock:
    """The lock every operation on one desk holds (settle, mark, apply, status).

    Pool cash and book links change in several writes; holding the desk's lock keeps a wake, a
    briefing's settlement and a status change from interleaving. The API is one process.
    """
    return _LOCKS.setdefault(desk_id, asyncio.Lock())


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
    mode: Literal["paper", "live"] = "paper"
    live_acknowledged: bool = False


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
    """Validate the timeframe and the universe against the quote currency, then insert.

    A live desk (ADR 0148) needs the operator's live acknowledgement, recorded on the desk; its
    orders still need the risk policy to reserve the desk's capital in ``desk_allocations``.
    """
    if spec.mode == "live" and not spec.live_acknowledged:
        raise DeskError("DESK_LIVE_ACK_REQUIRED: a live desk trades real money; acknowledge live.")
    if spec.timeframe not in EXECUTION_TIMEFRAMES:
        allowed = ", ".join(EXECUTION_TIMEFRAMES)
        raise DeskError(f"DESK_TIMEFRAME_INVALID: {spec.timeframe} is not one of {allowed}.")
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
        mode=spec.mode,
        live_acknowledged_at=now if spec.mode == "live" else None,
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
    async with desk_lock(desk_id):
        state = await _state(desk_id, store=store, execution=execution, market_data=market_data)
        wakes = await store.list_wakes(desk_id, limit=_HISTORY)
    rates = await execution.fee_rates()
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
    async with desk_lock(desk_id):
        state = await _state(desk_id, store=store, execution=execution, market_data=market_data)
        books = await store.list_books(desk_id)
        wakes = await store.list_wakes(desk_id, limit=_SCORED_WAKES)
    rates = await execution.fee_rates()
    return await score_desk(
        state.desk,
        equity=state.equity,
        books=books,
        wakes=wakes,
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
    async with desk_lock(desk_id):
        return await _record_wake(
            desk_id,
            store=store,
            execution=execution,
            market_data=market_data,
            raw_response=raw_response,
            harness=harness,
            model=model,
            briefing_fingerprint=briefing_fingerprint,
            started_at=started_at,
            harness_error=harness_error,
            moment=now or datetime.now(UTC),
        )


async def _record_wake(
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
    harness_error: str | None,
    moment: datetime,
) -> DeskWake:
    """:func:`record_wake` under the desk's lock."""
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
        opened = await _apply(
            decision, state=state, store=store, execution=execution, wake=decided, now=moment
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
        if opened is not None:
            return opened
        outcome = {"outcome": WakeOutcome.APPLIED, "deployment_id": decision.deployment_id}
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
    desk = await resolve_pending(desk, store=store, execution=execution)
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
    """Stop flat running books; settle flat stopped ones and credit the pool in one write."""
    returned = Decimal(0)
    settled: list[DeskBook] = []
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
        value = book_value(book, snapshot, None)
        if value is None:
            continue
        settled.append(
            book.model_copy(update={"settled_at": datetime.now(UTC), "settled_cash": value})
        )
        returned += value
    if not settled:
        return desk
    return await save_desk(
        desk.model_copy(update={"cash": desk.cash + returned}),
        store=store,
        previous=desk,
        books=settled,
    )


async def _roll_day(desk: Desk, *, store: DeskStore, equity: Decimal | None) -> Desk:
    """Record each UTC day's opening equity for the daily loss cap.

    The opening equity is the last equity journaled before midnight, however many wakes ago,
    so a loss realized between midnight and the first known equity still counts against the
    new day; a desk with no earlier equity (its first day) opens at the first equity seen.
    """
    now = datetime.now(UTC)
    if equity is None or (desk.day_open_at is not None and desk.day_open_at.date() == now.date()):
        return desk
    midnight = datetime.combine(now.date(), time(), tzinfo=UTC)
    before = await store.last_equity_before(desk.desk_id, midnight)
    opening = equity if before is None else before
    rolled = desk.model_copy(update={"day_open_equity": opening, "day_open_at": now})
    return await save_desk(rolled, store=store, previous=desk)


async def _apply(
    decision: DeskDecision,
    *,
    state: _State,
    store: DeskStore,
    execution: DeskExecution,
    wake: DeskWake,
    now: datetime,
) -> DeskWake | None:
    """Apply one checked decision; an open returns its wake, journaled with the book link."""
    if decision.action == "no_trade":
        return None
    if state.desk.status is not DeskStatus.ACTIVE:
        raise DeskError(f"DESK_NOT_ACTIVE: the desk is {state.desk.status.value}.")
    if decision.action == "close":
        if decision.deployment_id not in {book.deployment_id for book, _ in state.open_books}:
            raise DeskError("DESK_UNKNOWN_BOOK: that deployment is not an open book of this desk.")
        await execution.stop_book(decision.deployment_id, flatten=True)
        return None
    allocation = _check_open(decision, state)
    request = _order_request(decision, state.desk, wake_id=wake.wake_id, allocation=allocation)
    return await open_funded(
        state.desk,
        request=request,
        allocation=allocation,
        wake=wake,
        now=now,
        store=store,
        execution=execution,
    )


def _check_open(decision: DeskDecision, state: _State) -> Decimal:
    """Enforce the universe and caps; return the allocation in quote."""
    desk, caps = state.desk, state.desk.caps
    product_id = decision.product_id or ""
    if product_id not in desk.products:
        raise DeskError(f"DESK_PRODUCT_NOT_IN_UNIVERSE: {product_id} is not a desk product.")
    if desk.mode == "live" and decision.side == "short":
        raise DeskError("DESK_SHORT_LIVE_UNSUPPORTED: a live spot desk can only buy.")
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
    """The discretionary order for an open decision: a fresh book in the desk's mode.

    Paper books start with the allocation as their cash and the account's fee rates; live
    orders are sized to the allocation and admitted by the desk's policy reservation.
    """
    market = decision.entry == "market"
    header = f"[desk {desk.name} | {desk.harness}{'/' + desk.model if desk.model else ''}] "
    live = desk.mode == "live"
    return DiscretionaryOrderRequest(
        mode=DeploymentMode.LIVE if live else DeploymentMode.PAPER,
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
        paper_starting_cash=None if live else allocation,
        paper_maker_fee_rate=None,
        paper_taker_fee_rate=None,
        note=(header + decision.thesis)[:4000],
        fresh_book=True,
        desk_id=desk.desk_id,
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
        mode=desk.mode,
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
    equity = book_value(book, snapshot, mark)
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
    if "risk-policy allocations are in force" in message:
        return "DISCRETIONARY_NOT_ALLOCATED"
    return "DESK_ORDER_REFUSED"


async def _require_desk(store: DeskStore, desk_id: str) -> Desk:
    """Return the desk or refuse."""
    desk = await store.get_desk(desk_id)
    if desk is None:
        raise DeskError(f"DESK_NOT_FOUND: no desk {desk_id}.")
    return desk
