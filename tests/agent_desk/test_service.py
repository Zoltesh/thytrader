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
from thytrader.agent_desk.funding import created_by
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
    IntentOrigin,
    IntentPurpose,
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
        """The book a reserved open created: by its order's key, else by its funding."""
        intent = await self.store.get_intent_by_idempotency_key(pending.idempotency_key)
        if intent is not None:
            return str(intent.deployment_id)
        for deployment in await self.store.list_deployments(limit=200):
            if created_by(pending, deployment):
                return str(deployment.id)
        return None

    async def stop_book(self, deployment_id: str, *, flatten: bool) -> DeploymentSnapshot:
        """Stop one book."""
        return await set_deployment_status(
            store=self.store,
            deployment_id=UUID(deployment_id),
            status=DeploymentStatus.STOPPED,
            publication_store=DisabledStrategySnapshotStore(),
            flatten=flatten,
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
    store: InMemoryDeskStore, execution: _Execution, desk_id: str, answer: object
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
        briefing_fingerprint="sha256:" + "0" * 64,
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


async def test_a_reserved_book_without_its_order_is_linked_not_refunded() -> None:
    """A book created before its order was recorded is found by its funding, not refunded."""
    store, execution, desk_id = await _desk()
    desk = await store.get_desk(desk_id)
    assert desk is not None
    now = datetime.now(UTC)
    pending = PendingOpen(
        wake_id="w-1",
        idempotency_key=f"desk:{desk_id}:w-1",
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
    orphan = replace(
        _pending_entry_book(),
        product_id="BTC-USD",
        created_at=now + timedelta(seconds=1),
        updated_at=now + timedelta(seconds=1),
    )
    await execution.store.create_deployment(orphan)
    await briefing(desk_id, store=store, execution=execution, market_data=execution.market_data)
    resolved = await store.get_desk(desk_id)
    assert resolved is not None
    assert resolved.pending_open is None
    assert resolved.cash == Decimal(9000)
    assert [book.deployment_id for book in await store.list_books(desk_id)] == [str(orphan.id)]


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
