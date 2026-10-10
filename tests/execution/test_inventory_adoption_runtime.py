"""Execution paths that assumed every order went to a venue, against adopted books (ADR 0124)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from tests.adoption_support import (
    ADOPTED_AT,
    MARK_BAR,
    adopted_book,
    executed_fill,
    live_book,
    persist_adoption,
    records_for,
)
from tests.execution.test_lifecycle_safety import _restart
from tests.execution.test_live_bracket import _candle, _product, _RecordingBroker, _strategy
from thytrader.exchanges.coinbase_broker import CoinbaseRestBroker
from thytrader.exchanges.coinbase_order_json import _order_configuration
from thytrader.execution.broker import BrokerError, SubmitResult
from thytrader.execution.execution_quality import build_execution_quality_report
from thytrader.execution.execution_quality_journal import _earliest_fill_bars
from thytrader.execution.execution_quality_models import (
    ExecutionQualityEvidenceReason,
    JournaledDecisionClose,
    TwinComparisonReason,
)
from thytrader.execution.execution_quality_twin import _fee_normalization
from thytrader.execution.fill_comparison import entry_fill_stats
from thytrader.execution.loop import cancel_risk_increasing_orders, maintain_open_inventory
from thytrader.execution.paper import PaperBroker
from thytrader.execution.reconcile import ADOPTION_EVIDENCE_INCOMPLETE_DETAIL, reconcile_open_orders
from thytrader.execution.runtime_ops import _active_entry
from thytrader.execution.submit import submit_intent
from thytrader.trading.adoption import ADOPTION_EVIDENCE_INCOMPLETE
from thytrader.trading.ids import uuid7
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentSnapshot,
    DeploymentStatus,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

pytestmark = pytest.mark.anyio


class _NoRequestTransport:
    """Signed transport that fails the test on any venue request."""

    def get(self, path: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        """No GET may be sent for an adoption."""
        raise AssertionError(f"unexpected GET {path} {params}")

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """No POST may be sent for an adoption."""
        raise AssertionError(f"unexpected POST {path} {data}")


@dataclass
class _ReadRecordingBroker(_RecordingBroker):
    """Live-style broker that also records every order read."""

    reads: list[str] = field(default_factory=list)

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Record the read; answer like the recording broker."""
        self.reads.append(client_order_id)
        return await super().get_order(
            venue_order_id=venue_order_id, client_order_id=client_order_id
        )


def _working(
    snapshot: DeploymentSnapshot,
    *,
    purpose: IntentPurpose,
    kind: OrderKind,
    side: OrderSide,
    minutes: int,
    known_intent: bool = True,
) -> DeploymentSnapshot:
    """Add one OPEN venue order (and, when known, its intent) to a snapshot."""
    at = ADOPTED_AT + timedelta(minutes=minutes)
    deployment = snapshot.deployment
    intent = OrderIntent(
        id=uuid7(at),
        deployment_id=deployment.id,
        client_order_id=f"{purpose.value}-working-{minutes}",
        purpose=purpose,
        side=side,
        kind=kind,
        quantity=Decimal(10),
        created_at=at,
        candle_starts_at=MARK_BAR,
        product_id=deployment.product_id,
    )
    order = Order(
        id=uuid7(at),
        deployment_id=deployment.id,
        intent_id=intent.id,
        client_order_id=intent.client_order_id,
        side=side,
        kind=kind,
        quantity=Decimal(10),
        status=OrderStatus.OPEN,
        created_at=at,
        updated_at=at,
        price=Decimal("0.2"),
        venue_order_id=f"venue-{intent.client_order_id}",
        product_id=deployment.product_id,
    )
    intents = (*snapshot.intents, intent) if known_intent else snapshot.intents
    return replace(snapshot, intents=intents, orders=(*snapshot.orders, order))


async def _stored(snapshot: DeploymentSnapshot) -> InMemoryExecutionStore:
    """A fresh store holding exactly the snapshot's rows."""
    return await _restart(snapshot)


async def test_paper_and_coinbase_brokers_refuse_adoption_before_any_request() -> None:
    """An adoption that reached a broker would otherwise become a real post-only limit."""
    with pytest.raises(ValueError, match="never routed"):
        await PaperBroker().place_order(
            client_order_id="adopt:x",
            product_id="DOGE-USD",
            side=OrderSide.BUY,
            kind=OrderKind.ADOPTION,
            quantity=Decimal(100),
            price=Decimal("0.2"),
        )
    with pytest.raises(BrokerError, match="never routed"):
        _order_configuration(OrderKind.ADOPTION, OrderSide.BUY, Decimal(100), Decimal("0.2"), None)
    with pytest.raises(BrokerError, match="never routed"):
        await CoinbaseRestBroker(_NoRequestTransport()).place_order(
            client_order_id="adopt:x",
            product_id="DOGE-USD",
            side=OrderSide.BUY,
            kind=OrderKind.ADOPTION,
            quantity=Decimal(100),
            price=Decimal("0.2"),
        )


@pytest.mark.parametrize(
    ("purpose", "kind"),
    [
        (IntentPurpose.ADOPTION, OrderKind.ADOPTION),
        (IntentPurpose.ENTRY, OrderKind.ADOPTION),
        (IntentPurpose.ADOPTION, OrderKind.MARKETABLE),
    ],
)
async def test_submit_intent_refuses_adoption_without_writing_anything(
    purpose: IntentPurpose, kind: OrderKind
) -> None:
    """Adoption bypasses submission; a misroute leaves no PENDING or UNKNOWN order."""
    store = InMemoryExecutionStore()
    book = await store.create_deployment(live_book())
    broker = _RecordingBroker()
    with pytest.raises(ValueError, match="never submitted"):
        await submit_intent(
            store=store,
            broker=broker,
            deployment_id=book.id,
            product_id=book.product_id,
            purpose=purpose,
            side=OrderSide.BUY,
            kind=kind,
            quantity=Decimal(100),
            price=Decimal("0.2"),
            candle=_candle(1),
        )
    snapshot = await store.get_deployment(book.id)
    assert snapshot.intents == () and snapshot.orders == () and broker.placed == []


async def test_reconcile_never_reads_a_complete_adoption_from_the_venue() -> None:
    """A FILLED order without a venue id is not an unconfirmed submit (no misleading pause)."""
    store = InMemoryExecutionStore()
    adopted = await adopted_book(store)
    broker = _ReadRecordingBroker()
    result = await reconcile_open_orders(adopted, broker=broker, store=store, product_id="DOGE-USD")
    assert broker.reads == [] and broker.placed == []
    assert result.deployment.status is DeploymentStatus.RUNNING
    assert result.deployment.mismatch_detail is None
    assert result.position is not None and result.position.quantity == Decimal(100)


@pytest.mark.parametrize(
    ("fault", "first_detail"),
    [
        ("missing_fill", ADOPTION_EVIDENCE_INCOMPLETE_DETAIL),
        ("unapplied_fill", "Stored fills have unapplied economics."),
        ("partial_fill", "Applied fills contain unprojected inventory."),
    ],
)
async def test_reconcile_pauses_on_incomplete_adoption_evidence(
    fault: str, first_detail: str
) -> None:
    """Incomplete adoption evidence pauses the book; it is never re-projected or read."""
    store = InMemoryExecutionStore()
    book = await store.create_deployment(live_book())
    records = records_for(book)
    await store.save_intent(records.intent)
    await store.save_order(records.order)
    if fault == "unapplied_fill":
        await store.save_fill(replace(records.fill, economics_applied_at=None))
    elif fault == "partial_fill":
        await store.save_fill(replace(records.fill, quantity=Decimal(40)))
    broker = _ReadRecordingBroker()
    result = await reconcile_open_orders(
        await store.get_deployment(book.id), broker=broker, store=store, product_id="DOGE-USD"
    )
    assert broker.reads == [] and broker.placed == []
    assert result.deployment.status is DeploymentStatus.PAUSED
    assert result.deployment.mismatch_detail == first_detail
    assert result.position is None and result.deployment.phase is RuntimePhase.FLAT
    if fault == "unapplied_fill":
        # Replay skipped the adoption fill: it stays unapplied and projects no position.
        assert [fill.economics_applied_at for fill in result.fills] == [None]
    assert ADOPTION_EVIDENCE_INCOMPLETE_DETAIL.startswith(ADOPTION_EVIDENCE_INCOMPLETE)


async def test_pause_cancel_keeps_the_working_exit_of_an_adopted_book() -> None:
    """Regression: with no ENTRY intent, a pause used to cancel every non-protection order."""
    adopted = await adopted_book(InMemoryExecutionStore())
    exiting = _working(
        adopted,
        purpose=IntentPurpose.STOP,
        kind=OrderKind.MARKETABLE,
        side=OrderSide.SELL,
        minutes=2,
    )
    store = await _stored(exiting)
    broker = _RecordingBroker()
    result = await cancel_risk_increasing_orders(
        await store.get_deployment(adopted.deployment.id), broker=broker, store=store
    )
    assert broker.canceled == []
    exit_order = next(order for order in result.orders if order.kind is OrderKind.MARKETABLE)
    assert exit_order.status is OrderStatus.OPEN


async def test_pause_cancel_still_cancels_entries_and_unknown_legacy_orders() -> None:
    """Known entries are cancelled; an unknown intent counts as an entry only without one."""
    adopted = await adopted_book(InMemoryExecutionStore())
    mixed = _working(
        _working(
            adopted,
            purpose=IntentPurpose.STOP,
            kind=OrderKind.MARKETABLE,
            side=OrderSide.SELL,
            minutes=2,
        ),
        purpose=IntentPurpose.ENTRY,
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        minutes=3,
    )
    store = await _stored(mixed)
    broker = _RecordingBroker()
    await cancel_risk_increasing_orders(
        await store.get_deployment(adopted.deployment.id), broker=broker, store=store
    )
    assert broker.canceled == ["venue-entry-working-3"]
    legacy = _working(
        replace(adopted, intents=(), orders=(), fills=()),
        purpose=IntentPurpose.ENTRY,
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        minutes=4,
        known_intent=False,
    )
    legacy_store = await _stored(legacy)
    legacy_broker = _RecordingBroker()
    await cancel_risk_increasing_orders(
        await legacy_store.get_deployment(adopted.deployment.id),
        broker=legacy_broker,
        store=legacy_store,
    )
    assert legacy_broker.canceled == ["venue-entry-working-4"]


async def test_working_exit_of_an_adopted_book_is_not_a_working_entry() -> None:
    """The entry fallback ignores orders whose intent is known and not an entry."""
    adopted = await adopted_book(InMemoryExecutionStore())
    exiting = _working(
        adopted,
        purpose=IntentPurpose.STOP,
        kind=OrderKind.MARKETABLE,
        side=OrderSide.SELL,
        minutes=2,
    )
    assert _active_entry(exiting) is None
    legacy = _working(
        adopted,
        purpose=IntentPurpose.ENTRY,
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        minutes=3,
        known_intent=False,
    )
    entry = _active_entry(legacy)
    assert entry is not None and entry.client_order_id == "entry-working-3"


async def test_protection_rests_for_adopted_coins_after_restart() -> None:
    """The ordinary protection path places a stop-and-target bracket for the adopted lot."""
    strategy = _strategy()
    adopted_at = _candle(2).starts_at + timedelta(minutes=10)
    book = live_book(
        product_id="BTC-USD",
        created_at=adopted_at - timedelta(minutes=5),
        strategy_id=strategy.strategy_id,
    )
    first = InMemoryExecutionStore()
    created = await first.create_deployment(book)
    records = records_for(
        created,
        quantity=Decimal("0.01"),
        mark=_candle(1).close,
        now=adopted_at,
        mark_bar=_candle(1).starts_at,
    )
    adopted = await persist_adoption(
        first,
        await first.get_deployment(created.id),
        records,
        stop_price=Decimal("90"),
        target_price=Decimal("120"),
    )
    store = await _restart(adopted)
    broker = _ReadRecordingBroker()
    result = await maintain_open_inventory(
        await store.get_deployment(created.id),
        strategy=strategy,
        product=_product(),
        candles=(_candle(0), _candle(1)),
        broker=broker,
        store=store,
    )
    assert records.order.client_order_id not in broker.reads
    assert [placed["kind"] for placed in broker.placed] == [OrderKind.TRIGGER_BRACKET]
    bracket = broker.placed[0]
    assert bracket["side"] is OrderSide.SELL and bracket["quantity"] == Decimal("0.01")
    assert bracket["stop_trigger_price"] == Decimal("90") and bracket["price"] == Decimal("120")
    assert result.deployment.status is DeploymentStatus.RUNNING
    assert result.position is not None and result.position.quantity == Decimal("0.01")


async def test_adopted_round_trip_is_reported_without_execution_metrics() -> None:
    """Execution quality keeps the adoption as the trip's opening but not as an execution."""
    adopted = await adopted_book(InMemoryExecutionStore())
    exit_bar = MARK_BAR + timedelta(hours=1)
    exited = executed_fill(
        adopted,
        purpose=IntentPurpose.STOP,
        side=OrderSide.SELL,
        quantity=Decimal(100),
        price=Decimal("0.22"),
        minutes=45,
        decision_bar=exit_bar,
    )
    closes = {
        ("DOGE-USD", exit_bar): JournaledDecisionClose(
            Decimal("0.221"), exit_bar + timedelta(hours=1)
        )
    }
    report = build_execution_quality_report(exited, journaled_closes=closes)
    trip = report.books[0].round_trips[0]
    assert trip.direction == "long" and trip.net_pnl == "1.99"
    assert trip.entries[0].adopted and trip.entries[0].liquidity is None
    assert trip.entries[0].slippage_bps is None and not trip.exits[0].adopted
    assert trip.slippage_fills_total == trip.slippage_fills_journaled == 1
    totals = report.totals
    assert totals.applied_fill_count == 2 and totals.adopted_fill_count == 1
    assert totals.slippage_fills_total == 1 and totals.ledger_realized_delta == "0"
    assert ExecutionQualityEvidenceReason.LIQUIDITY_NOT_RECORDED not in report.evidence.reasons
    assert report.evidence.complete, report.evidence.reasons
    orders = {order.id: order for order in exited.orders}
    assert _earliest_fill_bars(exited, exited.deployment, orders) == {"DOGE-USD": exit_bar}
    reasons: list[TwinComparisonReason] = []
    normalization = _fee_normalization(
        DeploymentSnapshot(deployment=live_book(), position=None, orders=(), fills=(), intents=()),
        report,
        reasons,
    )
    assert normalization is not None and normalization.fill_count == 1
    assert normalization.fills_without_liquidity_evidence == 0 and normalization.complete
    assert TwinComparisonReason.LIVE_FILLS_WITHOUT_LIQUIDITY_EVIDENCE not in reasons


async def test_entry_fill_comparison_ignores_the_adoption() -> None:
    """Paper/live entry statistics count venue entries only; an adoption rested nothing."""
    stats = entry_fill_stats(await adopted_book(InMemoryExecutionStore()))
    assert stats.entries_rested == stats.entries_filled == 0
