"""Discretionary inventory adoption: protect held coins, or sell them (ADR 0124).

``protect`` adopts into a running discretionary long book (reusing a flat one, refusing an
occupied one) after in-kind risk admission. It then rests the stop and take-profit
exactly as after a live entry fill. ``sell`` reduces risk, so the entry gate does not
apply. It adopts into a new book that is STOPPED with lifecycle FLATTEN and carries a
sentinel stop of one price increment. The execution worker's stopped-book flatten then
sells it: FLATTEN takes priority over protection, so no protective order is placed.
The API never submits that sale itself, so it cannot race the worker into a double sell.

Protect is allowed under fleet disarm because it only adds protection, and sell because
it only reduces risk. Both write one why-trade record and an ``inventory_adopted`` audit
event that carries the mark, the venue balance and every live book's claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from thytrader.audit_events import AuditEventOutcome
from thytrader.execution.adoption import (
    ADOPTION_BOOK_OCCUPIED,
    AdoptionAction,
    adopted_quantity,
    live_accounting_books,
    occupied_discretionary_book,
    read_balances_or_refuse,
    reusable_discretionary_book,
    venue_minimum_refusal,
    venue_rows,
)
from thytrader.execution.audit_scope import record_execution_audit
from thytrader.execution.live_protection import _ensure_exit_protection
from thytrader.execution.mark_context import closed_mark_context
from thytrader.memory.recording import maybe_record_submitted_intent
from thytrader.memory.trade_reason_scope import discretionary_trade_reason_scope, trade_reason_scope
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.gate import evaluate_new_deployment, evaluate_new_entry
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import RiskDecision, RiskReasonCode, RiskVerdict
from thytrader.risk.store import load_effective_policy
from thytrader.trading.adoption_write import (
    ADOPTION_REFUSED,
    AdoptionRefusedError,
    AdoptionWrite,
)
from thytrader.trading.exposure import counts_for_daily_loss
from thytrader.trading.geometry import base_currency, bracket_error_detail, bracket_is_valid
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.inventory_claims import base_availability, managed_base_claims
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionConflictError,
    IntentPurpose,
    LifecycleCommand,
    PositionSide,
    RuntimePhase,
)
from thytrader.trading.sizing import quantize_to_increment

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.execution.adoption import AdoptionRequest
    from thytrader.execution.broker import Broker
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.memory.store import ExperientialMemoryStore
    from thytrader.memory.trade_reason_scope import TradeReasonScope
    from thytrader.risk.models import ActiveRiskPolicy
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.trading.adoption_write import AdoptionCommit, BalanceReader
    from thytrader.trading.inventory_claims import BaseAvailability
    from thytrader.trading.store import ExecutionStore, InventoryAdoptionStore

_SELL_VERDICT = RiskVerdict(
    decision=RiskDecision.ALLOW,
    reason_code=RiskReasonCode.ALLOWED,
    detail="Sell-holdings reduces risk; the entry gate does not apply (ADR 0124).",
)


async def adopt_held_inventory(
    request: AdoptionRequest,
    *,
    store: ExecutionStore,
    adoption_store: InventoryAdoptionStore,
    market_data: MarketDataService,
    broker: Broker | None,
    read_balances: BalanceReader | None,
    live_quote_cash: Decimal | None,
    risk_store: RiskPolicyStore | None,
    memory_store: ExperientialMemoryStore | None,
) -> DeploymentSnapshot:
    """Protect or sell held coins; a replayed idempotency key returns its book.

    Raises:
        ExecutionConflictError: Live is not configured, the key belongs to another order,
            the mark is stale, or a check refuses (``AdoptionRefusedError`` names the code).
    """
    if broker is None:
        raise ExecutionConflictError("Live trading requires configured Coinbase credentials.")
    reader = read_balances_or_refuse(read_balances)
    replay = await _replayed_book(store, request)
    if replay is not None:
        return replay
    product, candle = await closed_mark_context(
        market_data, product_id=request.product_id, timeframe=request.timeframe
    )
    deployments = await store.list_deployments()
    books = await live_accounting_books(store, deployments)
    availability = base_availability(
        await venue_rows(reader),
        managed_base_claims(books, base_currency(request.product_id)),
        base_increment=product.base_increment,
    )
    quantity = adopted_quantity(request, availability=availability, product=product)
    refusal = venue_minimum_refusal(product, quantity=quantity, mark=candle.close)
    if refusal is not None:
        raise refusal
    active = await load_effective_policy(risk_store)
    context = _Context(
        request=request,
        product=product,
        candle=candle,
        quantity=quantity,
        availability=availability,
        live_quote_cash=live_quote_cash,
        active=active,
    )
    if request.action is AdoptionAction.PROTECT:
        return await _protect(
            context,
            store=store,
            adoption_store=adoption_store,
            broker=broker,
            reader=reader,
            deployments=deployments,
            books=books,
            memory_store=memory_store,
        )
    return await _sell(
        context, adoption_store=adoption_store, reader=reader, memory_store=memory_store
    )


@dataclass(frozen=True, slots=True)
class _Context:
    """The checked facts one adoption acts on."""

    request: AdoptionRequest
    product: MarketProduct
    candle: Candle
    quantity: Decimal
    availability: BaseAvailability
    live_quote_cash: Decimal | None
    active: ActiveRiskPolicy

    @property
    def mark(self) -> Decimal:
        """The closed candle's close."""
        return self.candle.close

    @property
    def notional(self) -> Decimal:
        """Quote value of the adopted coins at the mark."""
        return self.quantity * self.mark

    def write(
        self,
        book: Deployment,
        *,
        stop: Decimal,
        target: Decimal | None,
        new: bool,
    ) -> AdoptionWrite:
        """The adoption of exactly ``quantity`` into ``book``."""
        request = self.request
        return AdoptionWrite(
            deployment_id=book.id,
            product_id=request.product_id,
            quantity=self.quantity,
            mark=self.mark,
            mark_bar_starts_at=self.candle.starts_at,
            now=utc_now(),
            origin=request.origin,
            stop_price=stop,
            target_price=target,
            base_increment=self.product.base_increment,
            idempotency_key=request.idempotency_key,
            new_deployment=book if new else None,
            expected_revision=None if new else book.revision,
        )


async def _protect(
    context: _Context,
    *,
    store: ExecutionStore,
    adoption_store: InventoryAdoptionStore,
    broker: Broker,
    reader: BalanceReader,
    deployments: Sequence[Deployment],
    books: Sequence[DeploymentSnapshot],
    memory_store: ExperientialMemoryStore | None,
) -> DeploymentSnapshot:
    """Admit in kind, adopt into a flat discretionary book, then rest protection."""
    request = context.request
    product = context.product
    stop, target = _protect_levels(context)
    reusable = reusable_discretionary_book(deployments, product_id=request.product_id)
    if reusable is None:
        occupied = occupied_discretionary_book(deployments, product_id=request.product_id)
        if occupied is not None:
            raise AdoptionRefusedError(
                ADOPTION_BOOK_OCCUPIED,
                f"Discretionary book {occupied.id} already holds or works {request.product_id}.",
            )
        book = _adoption_book(context, status=DeploymentStatus.RUNNING)
        _require(
            evaluate_new_deployment(
                context.active.definition,
                mode=DeploymentMode.LIVE,
                product_id=request.product_id,
                strategy_id=None,
                paper_starting_cash=None,
                deployments=deployments,
                policy_source=context.active.source,
            )
        )
        target_book = DeploymentSnapshot(deployment=book, position=None)
    else:
        book = reusable
        target_book = next(item for item in books if item.deployment.id == reusable.id)
    verdict = _require(_in_kind_admission(context, target_book=target_book, books=books))
    write = context.write(book, stop=stop, target=target, new=reusable is None)
    with trade_reason_scope(_reason_scope(context, memory_store, verdict)):
        commit = await adoption_store.adopt_inventory(write, read_balances=reader)
        await _journal(context, commit)
        return await _ensure_exit_protection(
            commit.snapshot, candle=context.candle, product=product, broker=broker, store=store
        )


async def _sell(
    context: _Context,
    *,
    adoption_store: InventoryAdoptionStore,
    reader: BalanceReader,
    memory_store: ExperientialMemoryStore | None,
) -> DeploymentSnapshot:
    """Adopt into a STOPPED+FLATTEN book; the worker's stopped-book flatten sells it."""
    sentinel = context.product.price_increment
    if sentinel >= context.mark:
        raise AdoptionRefusedError(
            ADOPTION_REFUSED, "The mark is too low for a sentinel stop below it."
        )
    book = _adoption_book(
        context, status=DeploymentStatus.STOPPED, lifecycle=LifecycleCommand.FLATTEN
    )
    write = context.write(book, stop=sentinel, target=None, new=True)
    with trade_reason_scope(_reason_scope(context, memory_store, _SELL_VERDICT)):
        commit = await adoption_store.adopt_inventory(write, read_balances=reader)
        await _journal(context, commit)
    return commit.snapshot


def _protect_levels(context: _Context) -> tuple[Decimal, Decimal]:
    """Quantize the stop and take-profit; a long needs stop < mark < take-profit."""
    request = context.request
    increment = context.product.price_increment
    if request.stop_price is None or request.take_profit_price is None:
        raise AdoptionRefusedError(
            ADOPTION_REFUSED, "protect requires stop_price and take_profit_price."
        )
    stop = quantize_to_increment(request.stop_price, increment)
    target = quantize_to_increment(request.take_profit_price, increment, rounding=ROUND_HALF_UP)
    if stop <= 0 or not bracket_is_valid(
        side=PositionSide.LONG, entry=context.mark, stop=stop, take_profit=target
    ):
        raise ExecutionConflictError(bracket_error_detail(PositionSide.LONG))
    return stop, target


def _in_kind_admission(
    context: _Context,
    *,
    target_book: DeploymentSnapshot,
    books: Sequence[DeploymentSnapshot],
) -> RiskVerdict:
    """The entry gate with in-kind funding (no order bounds, rate or collar)."""
    request = context.request
    peers = tuple(
        item
        for item in books
        if item.deployment.id != target_book.deployment.id
        and counts_for_daily_loss(item.deployment.status)
    )
    return evaluate_new_entry(
        context.active.definition,
        mode=DeploymentMode.LIVE,
        proposed=ProposedEntry(
            product_id=request.product_id,
            strategy_id=None,
            notional=context.notional,
            quantity=context.quantity,
            funding="in_kind",
        ),
        snapshots=(*peers, target_book),
        live_quote_cash=context.live_quote_cash,
        observation=EntryObservation(
            as_of=utc_now(),
            proposed_price=context.mark,
            reference_price=context.mark,
            marks={request.product_id: context.mark},
        ),
    )


def _require(verdict: RiskVerdict) -> RiskVerdict:
    """Refuse on a deny; return the verdict for the why-trade record otherwise."""
    if verdict.decision is RiskDecision.DENY:
        raise ExecutionConflictError(f"{verdict.reason_code.value}: {verdict.detail}")
    return verdict


def _adoption_book(
    context: _Context,
    *,
    status: DeploymentStatus,
    lifecycle: LifecycleCommand = LifecycleCommand.NONE,
) -> Deployment:
    """A new live discretionary book whose ledger starts at cash 0 (ADR 0106).

    Performance capital is pinned to the adopted notional (ADR 0107), so drawdown and
    returns are measured against the coins the book took over.
    """
    now = utc_now()
    request = context.request
    zero = Decimal(0)
    return Deployment(
        id=uuid7(now),
        strategy_fingerprint=None,
        strategy_id=None,
        product_id=request.product_id,
        mode=DeploymentMode.LIVE,
        status=status,
        cash=zero,
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
        last_signal="inventory_adoption",
        kind=DeploymentKind.DISCRETIONARY,
        timeframe=request.timeframe,
        lifecycle_command=lifecycle,
        venue_available_quote=context.live_quote_cash,
        performance_capital_quote=context.notional,
        initial_equity=zero,
        baseline_equity=zero,
        high_water_mark_equity=zero,
        utc_day_open_equity=zero,
        utc_day_open_at=now,
    )


def _reason_scope(
    context: _Context, memory_store: ExperientialMemoryStore | None, verdict: RiskVerdict
) -> TradeReasonScope | None:
    """Why-trade attribution: the policy, the verdict and the operator's note."""
    request = context.request
    return discretionary_trade_reason_scope(
        memory_store,
        policy=context.active.definition,
        timeframe=request.timeframe,
        note=request.note,
        note_origin=None if request.note is None else request.origin.value,
        verdict=verdict,
    )


async def _journal(context: _Context, commit: AdoptionCommit) -> None:
    """One why-trade record for the adoption intent and one audit event with the figures."""
    await maybe_record_submitted_intent(intent=commit.records.intent, snapshot=commit.snapshot)
    figures = commit.availability
    claims = figures.claims
    await record_execution_audit(
        action="inventory_adopted",
        outcome=AuditEventOutcome.SUCCESS,
        detail=(
            f"deployment_id={commit.snapshot.deployment.id} "
            f"action={context.request.action.value} "
            f"client_order_id={commit.records.order.client_order_id} "
            f"quantity={commit.records.order.quantity} mark={context.mark} "
            f"mark_source=closed_candle timeframe={context.request.timeframe} "
            f"candle_starts_at={context.candle.starts_at.isoformat()} "
            f"balance_total={figures.total} balance_available={figures.available} "
            f"claimed={claims.claimed} managed_long={claims.managed_long} "
            f"working_buys={claims.working_buys} "
            f"working_short_entry_sells={claims.working_short_entry_sells} "
            f"adoptable={figures.adoptable}"
        ),
        product_id=context.request.product_id,
    )


async def _replayed_book(
    store: ExecutionStore, request: AdoptionRequest
) -> DeploymentSnapshot | None:
    """Return the book of an adoption already made with this key; refuse a foreign key."""
    existing = await store.get_intent_by_idempotency_key(request.idempotency_key)
    if existing is None:
        return None
    if existing.purpose is not IntentPurpose.ADOPTION or existing.product_id != request.product_id:
        raise ExecutionConflictError("idempotency_key already used by another order.")
    return await store.get_deployment(existing.deployment_id)
