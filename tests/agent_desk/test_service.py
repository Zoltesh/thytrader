"""A desk funds fresh paper books from its pool, enforces its caps and journals every wake."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
import json
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from tests.persistence.test_postgres_fill_transaction import _pending_entry_book
from thytrader.agent_desk.market import MarketRow, fresh_marks, newest_marks
from thytrader.agent_desk.models import (
    Desk,
    DeskBook,
    DeskCaps,
    DeskStatus,
    DeskWake,
    PendingOpen,
    WakeOutcome,
)
from thytrader.agent_desk.service import (
    DeskError,
    DeskSpec,
    briefing,
    create_desk,
    record_wake,
    score,
)
from thytrader.agent_desk.store import DeskStoreUnavailableError, InMemoryDeskStore
from thytrader.execution.discretionary import place_discretionary_order
from thytrader.execution.exit_guards import CANCEL_BEFORE_EXIT_DETAIL, FLAT_AFTER_FAULT_DETAIL
from thytrader.execution.paper import PaperBroker
from thytrader.execution.service import set_deployment_status
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.strategies.snapshots import DisabledStrategySnapshotStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionConflictError,
    ExecutionStoreError,
    IntentOrigin,
    IntentPurpose,
    LifecycleCommand,
    Order,
    OrderSide,
    OrderStatus,
    RuntimePhase,
    with_runtime,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.agent_desk.models import DeskWake
    from thytrader.execution.discretionary_request import DiscretionaryOrderRequest
    from thytrader.trading.store import ExecutionStore

pytestmark = pytest.mark.anyio
_RATES = (Decimal("0.004"), Decimal("0.006"))


class _Execution:
    """Real discretionary paper books on demo candles, with fixed account fee rates."""

    def __init__(self) -> None:
        """Start with an empty execution store."""
        self.store: ExecutionStore = InMemoryExecutionStore()
        self.market_data = MarketDataService(DemoMarketData())

    async def snapshot(self, deployment_id: str) -> DeploymentSnapshot:
        """Return one book."""
        return await self.store.get_accounting_snapshot(UUID(deployment_id))

    async def open_book(self, request: DiscretionaryOrderRequest) -> DeploymentSnapshot:
        """Place on a fresh paper book (the account fee read is the fixed rates here)."""
        return await place_discretionary_order(
            store=self.store,
            broker=PaperBroker(),
            market_data=self.market_data,
            request=replace(
                request, paper_maker_fee_rate=_RATES[0], paper_taker_fee_rate=_RATES[1]
            ),
            live_allowed=False,
        )

    async def find_book(self, pending: PendingOpen) -> str | None:
        """The reservation's own book: the pre-chosen id, in the desk's mode, on its product.

        A reservation written before book ids were chosen is found by its order's key.
        """
        if pending.deployment_id is None:
            return await self.intent_book(pending.idempotency_key)
        try:
            snapshot = await self.store.get_deployment(UUID(pending.deployment_id))
        except ExecutionStoreError as error:
            if "not found" in str(error).lower():
                return None
            raise
        deployment = snapshot.deployment
        if deployment.mode.value != pending.mode or deployment.product_id != pending.product_id:
            message = f"Book {pending.deployment_id} does not match its desk reservation."
            raise ExecutionStoreError(message)
        return str(deployment.id)

    async def intent_book(self, idempotency_key: str) -> str | None:
        """The book an order placed under ``idempotency_key`` belongs to, if one was placed."""
        intent = await self.store.get_intent_by_idempotency_key(idempotency_key)
        return None if intent is None else str(intent.deployment_id)

    async def stop_book(
        self, deployment_id: str, *, flatten: bool, expected_revision: int | None = None
    ) -> DeploymentSnapshot:
        """Stop one book."""
        return await set_deployment_status(
            store=self.store,
            deployment_id=UUID(deployment_id),
            status=DeploymentStatus.STOPPED,
            publication_store=DisabledStrategySnapshotStore(),
            flatten=flatten,
            expected_revision=expected_revision,
        )

    async def fee_rates(self) -> tuple[Decimal, Decimal] | None:
        """The account's paper rates."""
        return _RATES


async def _desk(**changes: object) -> tuple[InMemoryDeskStore, _Execution, str]:
    """A BTC-USD desk with $10,000 and default caps."""
    store = InMemoryDeskStore()
    spec = DeskSpec(
        name="test desk",
        quote_currency="USD",
        starting_cash=Decimal(10000),
        products=("BTC-USD", "ETH-USD"),
    )
    desk = await create_desk(store, replace(spec, **changes), now=datetime.now(UTC))
    return store, _Execution(), desk.desk_id


async def _wake(
    store: InMemoryDeskStore,
    execution: _Execution,
    desk_id: str,
    answer: object,
    fingerprint: str | None = None,
) -> DeskWake:
    """Record one wake with ``answer`` as the model's text."""
    text = answer if isinstance(answer, str) else json.dumps(answer)
    return await record_wake(
        desk_id,
        store=store,
        execution=execution,
        market_data=execution.market_data,
        raw_response=text,
        harness="test",
        model=None,
        briefing_fingerprint=fingerprint or "sha256:" + uuid4().hex + uuid4().hex,
        started_at=datetime.now(UTC),
    )


def _open(product: str = "BTC-USD", fraction: str = "0.1", **changes: object) -> dict:
    """An open decision with stops far enough from any demo price."""
    return {
        "action": "open",
        "thesis": "test thesis",
        "product_id": product,
        "side": "long",
        "size_fraction": fraction,
        "entry": "market",
        "stop_price": "1",
        "take_profit_price": "100000000",
        **changes,
    }


async def test_a_desk_universe_must_match_its_quote_currency() -> None:
    """A USDC product on a USD desk is refused at creation."""
    with pytest.raises(DeskError, match="DESK_PRODUCT_INVALID"):
        await _desk(products=("BTC-USDC",))


async def test_a_desk_timeframe_must_be_an_execution_timeframe() -> None:
    """An unusable timeframe is refused at creation, not at the first open."""
    with pytest.raises(DeskError, match="DESK_TIMEFRAME_INVALID"):
        await _desk(timeframe="nonsense")


async def test_the_briefing_carries_the_pool_market_and_one_prompt() -> None:
    """Equity equals the pool before any trade; the prompt embeds the fingerprinted briefing."""
    store, execution, desk_id = await _desk()
    brief = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    assert brief.desk.equity_quote == Decimal(10000)
    assert brief.desk.new_entries_allowed
    assert brief.desk.taker_fee_rate == _RATES[1]
    assert {row.product_id for row in brief.market} == {"BTC-USD", "ETH-USD"}
    assert brief.fingerprint.startswith("sha256:")
    assert brief.fingerprint in brief.prompt
    assert "exactly one JSON object" in brief.prompt


async def test_no_trade_and_invalid_answers_are_journaled() -> None:
    """Every wake is recorded, including a malformed answer, which is never guessed at."""
    store, execution, desk_id = await _desk()
    quiet = await _wake(store, execution, desk_id, {"action": "no_trade", "thesis": "chop"})
    garbled = await _wake(store, execution, desk_id, "I think BTC goes up.")
    assert quiet.outcome is WakeOutcome.APPLIED
    assert garbled.outcome is WakeOutcome.INVALID
    assert garbled.outcome_code == "DESK_DECISION_INVALID"
    assert len(await store.list_wakes(desk_id, limit=10)) == 2


async def test_an_open_funds_a_fresh_agent_book_from_the_pool() -> None:
    """The allocation leaves the pool; the book is new, paper, agent-origin, with the thesis."""
    store, execution, desk_id = await _desk()
    wake = await _wake(store, execution, desk_id, _open(fraction="0.1"))
    assert wake.outcome is WakeOutcome.APPLIED, wake.detail
    desk = await store.get_desk(desk_id)
    assert desk is not None
    assert desk.cash == Decimal(9000)
    book = await execution.snapshot(wake.deployment_id or "")
    assert book.deployment.kind is DeploymentKind.DISCRETIONARY
    assert book.deployment.paper_starting_cash == Decimal(1000)
    entry = next(intent for intent in book.intents if intent.purpose is IntentPurpose.ENTRY)
    assert entry.origin is IntentOrigin.AGENT
    brief = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    assert brief.desk.open_positions == 1
    assert brief.positions[0].deployment_id == wake.deployment_id


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (_open(fraction="0.5"), "DESK_POSITION_TOO_LARGE"),
        (_open(product="SOL-USD"), "DESK_PRODUCT_NOT_IN_UNIVERSE"),
        ({"action": "close", "thesis": "x", "deployment_id": "nope"}, "DESK_UNKNOWN_BOOK"),
    ],
)
async def test_caps_refuse_whatever_the_model_says(answer: dict, code: str) -> None:
    """Refusals are journaled with their code and move no cash."""
    store, execution, desk_id = await _desk()
    wake = await _wake(store, execution, desk_id, answer)
    assert wake.outcome is WakeOutcome.REFUSED
    assert wake.outcome_code == code
    desk = await store.get_desk(desk_id)
    assert desk is not None
    assert desk.cash == Decimal(10000)


async def test_one_book_per_product_and_the_position_cap() -> None:
    """A second open on the same product, or past max_open_positions, is refused."""
    store, execution, desk_id = await _desk(caps=DeskCaps(max_open_positions=1))
    first = await _wake(store, execution, desk_id, _open())
    again = await _wake(store, execution, desk_id, _open())
    other = await _wake(store, execution, desk_id, _open(product="ETH-USD"))
    assert first.outcome is WakeOutcome.APPLIED, first.detail
    assert again.outcome_code == "DESK_PRODUCT_OCCUPIED"
    assert other.outcome_code == "DESK_MAX_POSITIONS"


async def test_a_paused_desk_journals_but_never_trades() -> None:
    """Decisions on a non-active desk are refused DESK_NOT_ACTIVE."""
    store, execution, desk_id = await _desk()
    desk = await store.get_desk(desk_id)
    assert desk is not None
    await store.save_desk(
        desk.model_copy(update={"status": DeskStatus.PAUSED}), expected_revision=desk.revision
    )
    wake = await _wake(store, execution, desk_id, _open())
    assert wake.outcome_code == "DESK_NOT_ACTIVE"


async def test_a_flat_stopped_book_returns_its_fill_value_to_the_pool() -> None:
    """Settlement returns the allocation plus sale proceeds minus cost and fees, not book cash."""
    store, execution, desk_id = await _desk()
    wake = await _wake(store, execution, desk_id, _open(fraction="0.1"))
    book = await execution.snapshot(wake.deployment_id or "")
    entry_fill = book.fills[0]
    entry_order = next(order for order in book.orders if order.id == entry_fill.order_id)
    exit_order = replace(
        entry_order, id=uuid4(), side=OrderSide.SELL, client_order_id="exit", intent_id=uuid4()
    )
    await execution.store.save_order(exit_order)
    exit_price = entry_fill.price * Decimal("1.05")
    exit_fee = Decimal("1.25")
    await execution.store.save_fill(
        replace(
            entry_fill,
            id=uuid4(),
            order_id=exit_order.id,
            venue_fill_id="exit-fill",
            price=exit_price,
            fee=exit_fee,
        )
    )
    closed = with_runtime(
        book.deployment,
        updated_at=datetime.now(UTC),
        status=DeploymentStatus.STOPPED,
        phase=RuntimePhase.FLAT,
        cash=Decimal("123456"),
    )
    await execution.store.save_deployment(closed)
    for order in book.orders:
        if order.status in {OrderStatus.OPEN, OrderStatus.PENDING}:
            await execution.store.save_order(replace(order, status=OrderStatus.CANCELED))
    await execution.store.save_position(None, deployment_id=book.deployment.id)
    brief = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    expected = (
        Decimal(1000)
        - entry_fill.price * entry_fill.quantity
        - entry_fill.fee
        + exit_price * entry_fill.quantity
        - exit_fee
    )
    assert brief.desk.open_positions == 0
    assert brief.desk.cash == Decimal(9000) + expected


async def test_unapplied_fill_evidence_holds_settlement() -> None:
    """A fill recorded but not yet applied could still move cash: the pool waits for it."""
    store, execution, desk_id = await _desk()
    wake = await _wake(store, execution, desk_id, _open(fraction="0.1"))
    book = await execution.snapshot(wake.deployment_id or "")
    entry_fill = book.fills[0]
    entry_order = next(order for order in book.orders if order.id == entry_fill.order_id)
    exit_order = replace(
        entry_order, id=uuid4(), side=OrderSide.SELL, client_order_id="exit", intent_id=uuid4()
    )
    await execution.store.save_order(exit_order)
    exit_price = entry_fill.price * Decimal("1.05")
    exit_fee = Decimal("1.25")
    await execution.store.save_fill(
        replace(
            entry_fill,
            id=uuid4(),
            order_id=exit_order.id,
            venue_fill_id="exit-fill",
            price=exit_price,
            fee=exit_fee,
            economics_applied_at=None,
        )
    )
    closed = with_runtime(
        book.deployment,
        updated_at=datetime.now(UTC),
        status=DeploymentStatus.STOPPED,
        phase=RuntimePhase.FLAT,
        cash=Decimal("123456"),
    )
    await execution.store.save_deployment(closed)
    for order in book.orders:
        if order.status in {OrderStatus.OPEN, OrderStatus.PENDING}:
            await execution.store.save_order(replace(order, status=OrderStatus.CANCELED))
    await execution.store.save_position(None, deployment_id=book.deployment.id)
    brief = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    assert brief.desk.open_positions == 1
    assert brief.desk.cash == Decimal(9000)


async def test_a_harness_failure_is_journaled_as_an_error_wake() -> None:
    """No answer is parsed or applied; the journal says the harness failed."""
    store, execution, desk_id = await _desk()
    wake = await record_wake(
        desk_id,
        store=store,
        execution=execution,
        market_data=execution.market_data,
        raw_response="",
        harness="test",
        model=None,
        briefing_fingerprint="sha256:" + "0" * 64,
        started_at=datetime.now(UTC),
        harness_error="DESK_HARNESS_TIMEOUT: no answer within 600s.",
    )
    assert wake.outcome is WakeOutcome.ERROR
    assert wake.outcome_code == "DESK_HARNESS_FAILED"
    assert wake.detail is not None
    assert wake.detail.startswith("DESK_HARNESS_TIMEOUT")


async def test_the_score_sets_the_desk_beside_fee_paying_buy_and_hold() -> None:
    """Both benchmarks are priced from the desk's start; the desk is flat at its pool."""
    store = InMemoryDeskStore()
    execution = _Execution()
    created = await create_desk(
        store,
        DeskSpec(
            name="scored",
            quote_currency="USD",
            starting_cash=Decimal(10000),
            products=("BTC-USD", "ETH-USD"),
        ),
        now=datetime.now(UTC) - timedelta(days=3),
    )
    await _wake(store, execution, created.desk_id, {"action": "no_trade", "thesis": "wait"})
    result = await score(
        created.desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    assert result.return_fraction == Decimal(0)
    assert result.wakes == 1
    assert result.wakes_by_outcome["applied"] == 1
    assert result.btc.products == ("BTC-USD",)
    assert result.btc.return_fraction is not None, result.btc.unavailable_reason
    assert result.universe.return_fraction is not None, result.universe.unavailable_reason
    assert result.beats_btc == (Decimal(0) > result.btc.return_fraction)


async def test_concurrent_wakes_never_spend_the_pool_twice() -> None:
    """Simultaneous opens and a settling briefing are serialized: the pool pays for each book."""
    store, execution, desk_id = await _desk(caps=DeskCaps(max_open_positions=5))
    wakes = await asyncio.gather(
        _wake(store, execution, desk_id, _open(product="BTC-USD", fraction="0.2")),
        _wake(store, execution, desk_id, _open(product="ETH-USD", fraction="0.2")),
        briefing(desk_id, store=store, execution=execution, market_data=execution.market_data),
    )
    applied = [wake for wake in wakes[:2] if wake.outcome is WakeOutcome.APPLIED]
    desk = await store.get_desk(desk_id)
    books = await store.list_books(desk_id)
    assert desk is not None
    assert len(applied) == 2, [wake.detail for wake in wakes[:2]]
    assert len(books) == 2
    assert desk.cash + sum(book.allocated_quote for book in books) == Decimal(10000)


class _RefusingExecution(_Execution):
    """Every placement is refused by the execution path."""

    async def open_book(self, request: DiscretionaryOrderRequest) -> DeploymentSnapshot:
        """Refuse like an unreadable account fee source."""
        del request
        raise ExecutionConflictError("PAPER_FEES_UNAVAILABLE: account rates unreadable.")


async def test_a_refused_placement_moves_no_cash() -> None:
    """The pool is debited only together with linking a placed book."""
    store, _, desk_id = await _desk()
    execution = _RefusingExecution()
    wake = await _wake(store, execution, desk_id, _open())
    desk = await store.get_desk(desk_id)
    assert wake.outcome_code == "PAPER_FEES_UNAVAILABLE"
    assert desk is not None
    assert desk.cash == Decimal(10000)
    assert await store.list_books(desk_id) == ()


async def test_a_new_day_opens_at_the_last_equity_journaled_before_midnight() -> None:
    """A loss realized after midnight but before the first wake still counts today."""
    store, execution, desk_id = await _desk()
    desk = await store.get_desk(desk_id)
    assert desk is not None
    yesterday = datetime.now(UTC) - timedelta(days=1)
    late = datetime.combine(datetime.now(UTC).date(), time(), tzinfo=UTC) - timedelta(minutes=5)
    await store.save_desk(
        desk.model_copy(update={"day_open_at": yesterday, "day_open_equity": Decimal(10000)}),
        expected_revision=desk.revision,
    )
    quiet = await _wake(store, execution, desk_id, {"action": "no_trade", "thesis": "x"})
    await store.add_wake(
        quiet.model_copy(
            update={"wake_id": "pre-midnight", "started_at": late, "equity_quote": Decimal(10600)}
        )
    )
    reloaded = await store.get_desk(desk_id)
    assert reloaded is not None
    await store.save_desk(
        reloaded.model_copy(update={"day_open_at": yesterday}),
        expected_revision=reloaded.revision,
    )
    brief = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    rolled = await store.get_desk(desk_id)
    assert rolled is not None
    assert rolled.day_open_equity == Decimal(10600)
    assert brief.desk.equity_quote == Decimal(10000)


class _LinkFailingStore(InMemoryDeskStore):
    """Fails the first write that links a book, like a crash after placement."""

    def __init__(self) -> None:
        """Arm one failure."""
        super().__init__()
        self.fail_link = True

    async def save_desk(
        self,
        desk: Desk,
        *,
        expected_revision: int,
        books: Sequence[DeskBook] = (),
        wakes: Sequence[DeskWake] = (),
    ) -> Desk:
        """Raise once on the link write."""
        if books and self.fail_link:
            self.fail_link = False
            raise DeskStoreUnavailableError("storage dropped")
        return await super().save_desk(
            desk, expected_revision=expected_revision, books=books, wakes=wakes
        )


async def test_an_open_interrupted_before_its_link_is_linked_on_recovery() -> None:
    """The reservation paid for the book; the next read links it and journals the open."""
    store = _LinkFailingStore()
    execution = _Execution()
    desk = await create_desk(
        store,
        DeskSpec(
            name="crash", quote_currency="USD", starting_cash=Decimal(10000), products=("BTC-USD",)
        ),
        now=datetime.now(UTC),
    )
    with pytest.raises(DeskStoreUnavailableError):
        await _wake(store, execution, desk.desk_id, _open(fraction="0.1"))
    pending = await store.get_desk(desk.desk_id)
    assert pending is not None
    assert pending.pending_open is not None
    assert pending.cash == Decimal(9000)
    brief = await briefing(
        desk.desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    recovered = await store.get_desk(desk.desk_id)
    books = await store.list_books(desk.desk_id)
    wakes = await store.list_wakes(desk.desk_id, limit=10)
    assert recovered is not None
    assert recovered.pending_open is None
    assert recovered.cash == Decimal(9000)
    assert [book.allocated_quote for book in books] == [Decimal(1000)]
    assert [wake.outcome_code for wake in wakes] == ["DESK_OPEN_RECOVERED"]
    assert brief.desk.open_positions == 1


async def test_a_desk_holds_two_products_at_once() -> None:
    """The second open's daily-loss evidence marks the first book's holding too."""
    store, execution, desk_id = await _desk()
    first = await _wake(store, execution, desk_id, _open(product="BTC-USD"))
    second = await _wake(store, execution, desk_id, _open(product="ETH-USD"))
    assert first.outcome is WakeOutcome.APPLIED, first.detail
    assert second.outcome is WakeOutcome.APPLIED, second.detail
    assert len(await store.list_books(desk_id)) == 2


async def test_a_reservation_links_only_its_own_book_by_its_chosen_id() -> None:
    """Recovery finds the book by the id the reservation chose; another book is never claimed."""
    store, execution, desk_id = await _desk()
    desk = await store.get_desk(desk_id)
    assert desk is not None
    now = datetime.now(UTC)
    unrelated = replace(_pending_entry_book(), product_id="BTC-USD", created_at=now, updated_at=now)
    await execution.store.create_deployment(unrelated)
    own = replace(unrelated, id=uuid4())
    pending = PendingOpen(
        wake_id="w-1",
        idempotency_key=f"desk:{desk_id}:w-1",
        deployment_id=str(own.id),
        product_id="BTC-USD",
        side="long",
        allocated_quote=Decimal(1000),
        reserved_at=now,
        thesis="interrupted",
    )
    await store.save_desk(
        desk.model_copy(update={"cash": Decimal(9000), "pending_open": pending}),
        expected_revision=desk.revision,
    )
    await briefing(desk_id, store=store, execution=execution, market_data=execution.market_data)
    refunded = await store.get_desk(desk_id)
    assert refunded is not None
    assert refunded.cash == Decimal(10000)
    assert await store.list_books(desk_id) == ()
    await execution.store.create_deployment(own)
    again = refunded.model_copy(update={"cash": Decimal(9000), "pending_open": pending})
    await store.save_desk(again, expected_revision=refunded.revision)
    await briefing(desk_id, store=store, execution=execution, market_data=execution.market_data)
    linked = await store.get_desk(desk_id)
    assert linked is not None
    assert linked.cash == Decimal(9000)
    assert [book.deployment_id for book in await store.list_books(desk_id)] == [str(own.id)]


async def test_a_replayed_answer_never_opens_twice() -> None:
    """The same answer to the same briefing, posted again, is refused DESK_DUPLICATE_ANSWER."""
    store, execution, desk_id = await _desk()
    fingerprint = "sha256:" + "a" * 64
    first = await _wake(store, execution, desk_id, _open(), fingerprint=fingerprint)
    again = await _wake(
        store, execution, desk_id, _open(product="ETH-USD"), fingerprint=fingerprint
    )
    assert first.outcome is WakeOutcome.APPLIED, first.detail
    assert again.outcome_code == "DESK_DUPLICATE_ANSWER"
    books = await store.list_books(desk_id)
    desk = await store.get_desk(desk_id)
    assert [book.allocated_quote for book in books] == [Decimal(1000)]
    assert desk is not None
    assert desk.cash == Decimal(9000)


async def test_a_live_cancel_settles_only_after_its_watermark() -> None:
    """Until reconciliation settles a live cancel, a late fill could still arrive: hold the cash."""
    store, execution, desk_id = await _desk()
    first = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    close = next(row.last_close for row in first.market if row.product_id == "BTC-USD")
    assert close is not None
    limit = (close * Decimal("0.99")).quantize(Decimal("0.01"))
    wake = await _wake(
        store,
        execution,
        desk_id,
        _open(entry="limit", limit_price=str(limit), stop_price=str(limit / 2)),
    )
    assert wake.outcome is WakeOutcome.APPLIED, wake.detail
    book = await execution.snapshot(wake.deployment_id or "")
    assert not book.fills
    entry = book.orders[0]
    await execution.store.save_order(replace(entry, status=OrderStatus.CANCELED))
    await execution.store.save_deployment(
        with_runtime(
            replace(book.deployment, mode=DeploymentMode.LIVE),
            updated_at=datetime.now(UTC),
            status=DeploymentStatus.STOPPED,
            phase=RuntimePhase.FLAT,
        )
    )
    held = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    stored = (await execution.snapshot(wake.deployment_id or "")).orders[0]
    await execution.store.save_order(replace(stored, settled_at=datetime.now(UTC)))
    settled = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    assert held.desk.cash == Decimal(9000)
    assert settled.desk.cash == Decimal(10000)


async def test_a_legacy_reservation_without_a_book_id_is_found_by_its_order() -> None:
    """Reservations written before book ids existed recover through their order's key."""
    store, execution, desk_id = await _desk()
    fingerprint = "sha256:" + "b" * 64
    wake = await _wake(store, execution, desk_id, _open(), fingerprint=fingerprint)
    book = await execution.snapshot(wake.deployment_id or "")
    desk = await store.get_desk(desk_id)
    assert desk is not None
    key = book.intents[0].idempotency_key
    assert key is not None
    legacy = PendingOpen(
        wake_id=wake.wake_id,
        idempotency_key=key,
        product_id="BTC-USD",
        side="long",
        allocated_quote=Decimal(1000),
        reserved_at=datetime.now(UTC),
    )
    assert await execution.find_book(legacy) == wake.deployment_id


async def test_a_stale_close_is_not_a_mark() -> None:
    """A held book's close from long ago leaves its mark unknown."""
    store, execution, desk_id = await _desk()
    wake = await _wake(store, execution, desk_id, _open())
    book = await execution.snapshot(wake.deployment_id or "")
    current = await fresh_marks(execution.market_data, [book])
    stale = await fresh_marks(
        execution.market_data, [book], now=datetime.now(UTC) + timedelta(days=2)
    )
    assert "BTC-USD" in current
    assert stale == {}


def test_the_newer_close_wins_between_the_hourly_row_and_the_book_clock() -> None:
    """A 4h close that ended at 16:00 never replaces an hourly close that ended at 18:00."""
    hour_start = datetime(2026, 10, 22, 17, tzinfo=UTC)
    rows = [MarketRow(product_id="BTC-USD", last_close=Decimal(90), last_bar_starts_at=hour_start)]
    slow = {"BTC-USD": (Decimal(100), datetime(2026, 10, 22, 16, tzinfo=UTC))}
    fast = {"BTC-USD": (Decimal(95), datetime(2026, 10, 22, 18, 5, tzinfo=UTC))}
    assert newest_marks(rows, slow) == {"BTC-USD": Decimal(90)}
    assert newest_marks(rows, fast) == {"BTC-USD": Decimal(95)}


async def _stopped_out_book(
    store: InMemoryDeskStore, execution: _Execution, desk_id: str
) -> tuple[DeploymentSnapshot, Order]:
    """A book whose stop exit filled while its take-profit still rests: the stray order."""
    wake = await _wake(store, execution, desk_id, _open(fraction="0.1"))
    book = await execution.snapshot(wake.deployment_id or "")
    entry_fill = book.fills[0]
    entry_order = next(order for order in book.orders if order.id == entry_fill.order_id)
    stray = next(order for order in book.orders if order.status is OrderStatus.OPEN)
    exit_order = replace(
        entry_order, id=uuid4(), side=OrderSide.SELL, client_order_id="stop", intent_id=uuid4()
    )
    await execution.store.save_order(exit_order)
    await execution.store.save_fill(
        replace(entry_fill, id=uuid4(), order_id=exit_order.id, venue_fill_id="stop-fill")
    )
    await execution.store.save_position(None, deployment_id=book.deployment.id)
    await execution.store.save_deployment(
        with_runtime(book.deployment, updated_at=datetime.now(UTC), phase=RuntimePhase.FLAT)
    )
    return await execution.snapshot(wake.deployment_id or ""), stray


async def _flattened(
    store: InMemoryDeskStore, execution: _Execution, desk_id: str, book: DeploymentSnapshot
) -> bool:
    """Read the desk once and say whether it asked the worker to flatten ``book``."""
    await briefing(desk_id, store=store, execution=execution, market_data=execution.market_data)
    after = await execution.snapshot(str(book.deployment.id))
    return after.deployment.lifecycle_command is LifecycleCommand.FLATTEN


async def test_a_closed_book_resting_a_stray_exit_is_flattened_then_settled() -> None:
    """A stopped-out book whose take-profit still rests is cleaned up by the desk itself."""
    store, execution, desk_id = await _desk()
    book, stray = await _stopped_out_book(store, execution, desk_id)
    first = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    flattening = await execution.snapshot(str(book.deployment.id))
    assert flattening.deployment.lifecycle_command is LifecycleCommand.FLATTEN
    assert first.desk.cash == Decimal(9000)
    # The worker carries out the flatten: the stray order is cancelled, nothing is sold.
    await execution.store.save_order(replace(stray, status=OrderStatus.CANCELED))
    second = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    assert second.desk.open_positions == 0
    # Exited at the entry price: the round trip cost exactly the two fees.
    assert second.desk.cash == Decimal(10000) - 2 * book.fills[0].fee


async def test_a_resting_order_that_could_add_inventory_is_never_flattened() -> None:
    """A resting buy on a long book, or an entry-purpose order, is not a stray exit."""
    store, execution, desk_id = await _desk()
    book, stray = await _stopped_out_book(store, execution, desk_id)
    await execution.store.save_order(replace(stray, side=OrderSide.BUY))
    assert not await _flattened(store, execution, desk_id, book)
    entry_intent = next(i for i in book.intents if i.purpose is IntentPurpose.ENTRY)
    await execution.store.save_order(replace(stray, intent_id=entry_intent.id))
    assert not await _flattened(store, execution, desk_id, book)


_RETAINED_FAULTS = [
    CANCEL_BEFORE_EXIT_DETAIL,
    FLAT_AFTER_FAULT_DETAIL,
    "Market-data window is gapped or missing the latest closed bar.",
    "Order reconciliation is unconfirmed: order read failed.",
]


@pytest.mark.parametrize("fault", _RETAINED_FAULTS)
async def test_a_retained_fault_never_strands_a_closed_book(fault: str) -> None:
    """A closed book with final evidence is flattened whatever fault the worker left on it."""
    store, execution, desk_id = await _desk()
    book, _ = await _stopped_out_book(store, execution, desk_id)
    await execution.store.save_deployment(replace(book.deployment, mismatch_detail=fault))
    assert await _flattened(store, execution, desk_id, book)


@pytest.mark.parametrize("status", [DeploymentStatus.RUNNING, DeploymentStatus.STOPPED])
@pytest.mark.parametrize("fault", _RETAINED_FAULTS)
async def test_a_flat_book_with_a_retained_fault_still_settles(
    fault: str, status: DeploymentStatus
) -> None:
    """With every order terminal and evidence final, a leftover fault never locks pool cash.

    The settlement's stop clears it, even on a book that was already stopped.
    """
    store, execution, desk_id = await _desk()
    book, stray = await _stopped_out_book(store, execution, desk_id)
    await execution.store.save_order(replace(stray, status=OrderStatus.CANCELED))
    faulted = await execution.snapshot(str(book.deployment.id))
    await execution.store.save_deployment(
        replace(faulted.deployment, status=status, mismatch_detail=fault)
    )
    shown = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    assert shown.desk.open_positions == 0
    assert shown.desk.cash == Decimal(10000) - 2 * book.fills[0].fee
    settled = await execution.snapshot(str(book.deployment.id))
    assert settled.deployment.status is DeploymentStatus.STOPPED
    assert settled.deployment.mismatch_detail is None


async def test_a_venue_child_not_yet_imported_holds_settlement() -> None:
    """A parent naming a venue child with no local row may still rest an order at the venue."""
    store, execution, desk_id = await _desk()
    book, stray = await _stopped_out_book(store, execution, desk_id)
    await execution.store.save_order(replace(stray, status=OrderStatus.CANCELED))
    exit_order = next(order for order in book.orders if order.client_order_id == "stop")
    await execution.store.save_order(
        replace(exit_order, attached_child_venue_order_id="venue-child-not-imported")
    )
    held = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    assert held.desk.open_positions == 1
    child = replace(
        stray,
        id=uuid4(),
        client_order_id="imported-child",
        venue_order_id="venue-child-not-imported",
        parent_order_id=exit_order.id,
        status=OrderStatus.CANCELED,
    )
    await execution.store.save_order(child)
    settled = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    assert settled.desk.open_positions == 0


@pytest.mark.parametrize("status", [OrderStatus.PENDING, OrderStatus.UNKNOWN])
async def test_a_stray_order_of_unknown_venue_state_holds_the_flatten(
    status: OrderStatus,
) -> None:
    """Only an exit the venue confirmed open is cancelled; an unconfirmed one may have filled."""
    store, execution, desk_id = await _desk()
    book, stray = await _stopped_out_book(store, execution, desk_id)
    await execution.store.save_order(replace(stray, status=status))
    assert not await _flattened(store, execution, desk_id, book)


async def test_the_flatten_is_bound_to_the_revision_the_desk_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fault the worker writes after the desk's read refuses the stop instead of clearing."""
    store, execution, desk_id = await _desk()
    book, _ = await _stopped_out_book(store, execution, desk_id)
    stop_book = execution.stop_book

    async def worker_wins(
        deployment_id: str, *, flatten: bool, expected_revision: int | None = None
    ) -> DeploymentSnapshot:
        current = await execution.snapshot(deployment_id)
        await execution.store.save_deployment(
            replace(current.deployment, mismatch_detail="Venue order read failed.")
        )
        return await stop_book(deployment_id, flatten=flatten, expected_revision=expected_revision)

    monkeypatch.setattr(execution, "stop_book", worker_wins)
    assert not await _flattened(store, execution, desk_id, book)
    kept = await execution.snapshot(str(book.deployment.id))
    assert kept.deployment.mismatch_detail == "Venue order read failed."


async def test_a_live_cancel_before_its_watermark_holds_the_flatten() -> None:
    """A live cancel can still reveal a fill: keep the exit resting until it settles."""
    store, execution, desk_id = await _desk()
    book, stray = await _stopped_out_book(store, execution, desk_id)
    await execution.store.save_deployment(replace(book.deployment, mode=DeploymentMode.LIVE))
    remainder = replace(stray, id=uuid4(), client_order_id="rest", status=OrderStatus.CANCELED)
    await execution.store.save_order(remainder)
    assert not await _flattened(store, execution, desk_id, book)
    await execution.store.save_order(replace(remainder, settled_at=datetime.now(UTC)))
    assert await _flattened(store, execution, desk_id, book)


async def test_a_lost_stop_race_never_fails_a_read_or_a_wake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the worker wins the write, the desk skips the book and retries on its next read."""
    store, execution, desk_id = await _desk()
    book, _ = await _stopped_out_book(store, execution, desk_id)

    async def racing(*_: object, **__: object) -> DeploymentSnapshot:
        raise ExecutionConflictError("Deployment revision conflict.")

    monkeypatch.setattr(execution, "stop_book", racing)
    assert not await _flattened(store, execution, desk_id, book)
    wake = await _wake(store, execution, desk_id, {"action": "no_trade", "thesis": "wait"})
    assert wake.outcome is WakeOutcome.APPLIED, wake.detail


async def test_a_book_with_unapplied_fills_is_never_flattened_as_stranded() -> None:
    """Fills that net to zero but are not all applied may hide inventory: leave the book alone."""
    store, execution, desk_id = await _desk()
    wake = await _wake(store, execution, desk_id, _open(fraction="0.1"))
    book = await execution.snapshot(wake.deployment_id or "")
    entry_fill = book.fills[0]
    entry_order = next(order for order in book.orders if order.id == entry_fill.order_id)
    exit_order = replace(
        entry_order, id=uuid4(), side=OrderSide.SELL, client_order_id="stop", intent_id=uuid4()
    )
    await execution.store.save_order(exit_order)
    await execution.store.save_fill(
        replace(
            entry_fill,
            id=uuid4(),
            order_id=exit_order.id,
            venue_fill_id="stop-fill",
            economics_applied_at=None,
        )
    )
    await execution.store.save_position(None, deployment_id=book.deployment.id)
    await execution.store.save_deployment(
        with_runtime(book.deployment, updated_at=datetime.now(UTC), phase=RuntimePhase.FLAT)
    )
    await briefing(desk_id, store=store, execution=execution, market_data=execution.market_data)
    untouched = await execution.snapshot(wake.deployment_id or "")
    assert untouched.deployment.lifecycle_command is not LifecycleCommand.FLATTEN
