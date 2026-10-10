"""Choose and admit the discretionary book an on-demand entry rests on.

Reuses a flat running discretionary book or builds a new one, and fails closed unless
the risk registry admits both the book and the sized entry. A breaker denial pauses
the book, or its same-quote mode peers on a daily-loss breach, before raising.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.breaker_pause import _pause_mode_running
from thytrader.execution.paper_fees import paper_fee_rates
from thytrader.execution.runtime_ops import _pause
from thytrader.risk.accounting_evidence import accounting_snapshot
from thytrader.risk.beta_evidence import load_entry_beta
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.gate import evaluate_new_deployment, evaluate_new_entry
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import RiskDecision, RiskReasonCode, RiskVerdict, pauses_risk_increasing
from thytrader.risk.store import load_effective_policy
from thytrader.trading.exposure import counts_for_daily_loss
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.ledger import resolve_paper_fee_schedule
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionConflictError,
    LifecycleCommand,
    OrderStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.execution.discretionary_request import DiscretionaryOrderRequest
    from thytrader.execution.paper_fees import PaperFeeSource
    from thytrader.market_data.service import MarketDataService
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.trading.store import ExecutionStore


_OCCUPIED = {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}


async def _book_for_entry(
    store: ExecutionStore,
    *,
    request: DiscretionaryOrderRequest,
    risk_store: RiskPolicyStore | None,
    market_data: MarketDataService | None,
    notional: Decimal,
    quantity: Decimal,
    live_quote_cash: Decimal | None,
    entry_price: Decimal,
    reference_price: Decimal,
    paper_fee_source: PaperFeeSource | None = None,
) -> DeploymentSnapshot:
    """Reuse a flat running book or create one after the risk gate admits it.

    ``market_data`` supplies BTC-beta evidence when the policy sets a β cap (ADR 0125).
    """
    existing = await store.list_deployments()
    reusable = _reusable_book(existing, product_id=request.product_id, mode=request.mode)
    if reusable is not None:
        snapshot = await accounting_snapshot(store, reusable.id, as_of=utc_now())
        fresh = snapshot.deployment
        if (
            _reusable_book((fresh,), product_id=request.product_id, mode=request.mode) is None
            or snapshot.positions
            or snapshot.position is not None
            or fresh.lifecycle_command is not LifecycleCommand.NONE
            or any(
                order.status in {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}
                for order in snapshot.orders
            )
        ):
            raise ExecutionConflictError(
                "Discretionary candidate changed; read fresh state before retrying."
            )
        _require_matching_paper_fees(fresh, request)
        await _require_entry_admission(
            risk_store,
            store=store,
            market_data=market_data,
            request=request,
            snapshot=snapshot,
            notional=notional,
            quantity=quantity,
            live_quote_cash=live_quote_cash,
            deployments=existing,
            entry_price=entry_price,
            reference_price=reference_price,
        )
        return snapshot
    _reject_occupied_book(existing, product_id=request.product_id, mode=request.mode)
    await _require_book_admission(
        risk_store,
        request=request,
        deployments=existing,
    )
    if request.mode is DeploymentMode.PAPER:
        maker, taker = await paper_fee_rates(
            maker_fee_rate=request.paper_maker_fee_rate,
            taker_fee_rate=request.paper_taker_fee_rate,
            source=paper_fee_source,
        )
        request = replace(request, paper_maker_fee_rate=maker, paper_taker_fee_rate=taker)
    candidate = _new_discretionary_book(request, live_quote_cash=live_quote_cash)
    await _require_entry_admission(
        risk_store,
        store=store,
        market_data=market_data,
        request=request,
        snapshot=DeploymentSnapshot(deployment=candidate),
        notional=notional,
        quantity=quantity,
        live_quote_cash=live_quote_cash,
        deployments=existing,
        entry_price=entry_price,
        reference_price=reference_price,
    )
    created = await store.create_deployment(candidate)
    return await store.get_deployment(created.id)


def _new_discretionary_book(
    request: DiscretionaryOrderRequest, *, live_quote_cash: Decimal | None
) -> Deployment:
    """Build a flat discretionary book that has not been persisted yet."""
    now = utc_now()
    try:
        maker_fee_rate, taker_fee_rate = resolve_paper_fee_schedule(
            live=request.mode is DeploymentMode.LIVE,
            maker_fee_rate=request.paper_maker_fee_rate,
            taker_fee_rate=request.paper_taker_fee_rate,
        )
    except ValueError as error:
        raise ExecutionConflictError(str(error)) from error
    cash = _initial_cash(request, live_quote_cash=live_quote_cash)
    initial = cash if cash > 0 else live_quote_cash
    return Deployment(
        id=uuid7(now),
        strategy_fingerprint=None,
        strategy_id=None,
        product_id=request.product_id,
        mode=request.mode,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=request.paper_starting_cash,
        paper_maker_fee_rate=maker_fee_rate,
        paper_taker_fee_rate=taker_fee_rate,
        cash=cash,
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
        kind=DeploymentKind.DISCRETIONARY,
        timeframe=request.timeframe,
        venue_available_quote=live_quote_cash,
        initial_equity=initial,
        baseline_equity=initial,
        high_water_mark_equity=initial,
        utc_day_open_equity=initial,
        utc_day_open_at=now if initial is not None else None,
    )


def _initial_cash(
    request: DiscretionaryOrderRequest, *, live_quote_cash: Decimal | None
) -> Decimal:
    """Paper uses starting cash; live uses remaining quote when known."""
    if request.mode is DeploymentMode.PAPER:
        cash = request.paper_starting_cash
        if cash is None or cash <= 0:
            raise ExecutionConflictError(
                "Paper discretionary orders require positive starting cash."
            )
        return cash
    if live_quote_cash is None:
        return Decimal("0")
    return live_quote_cash


def _reusable_book(
    deployments: tuple[Deployment, ...],
    *,
    product_id: str,
    mode: DeploymentMode,
) -> Deployment | None:
    """Return a flat running discretionary book that can accept a new entry."""
    matches = [
        item
        for item in deployments
        if item.kind is DeploymentKind.DISCRETIONARY
        and item.product_id == product_id
        and item.mode is mode
        and item.status is DeploymentStatus.RUNNING
        and item.phase is RuntimePhase.FLAT
    ]
    return matches[0] if matches else None


def _reject_occupied_book(
    deployments: tuple[Deployment, ...],
    *,
    product_id: str,
    mode: DeploymentMode,
) -> None:
    """Conflict when another occupied discretionary book already owns this product."""
    occupied = [
        item
        for item in deployments
        if item.kind is DeploymentKind.DISCRETIONARY
        and item.product_id == product_id
        and item.mode is mode
        and item.status in _OCCUPIED
    ]
    if occupied:
        raise ExecutionConflictError(
            "An occupied discretionary book already exists for this product and mode."
        )


async def _require_book_admission(
    risk_store: RiskPolicyStore | None,
    *,
    request: DiscretionaryOrderRequest,
    deployments: tuple[Deployment, ...],
) -> None:
    """Fail closed when the registry rejects a new discretionary book."""
    if request.mode is DeploymentMode.PAPER and (
        request.paper_starting_cash is None or request.paper_starting_cash <= 0
    ):
        raise ExecutionConflictError("Paper discretionary orders require positive starting cash.")
    active = await load_effective_policy(risk_store)
    verdict = evaluate_new_deployment(
        active.definition,
        mode=request.mode,
        product_id=request.product_id,
        strategy_id=None,
        paper_starting_cash=request.paper_starting_cash,
        deployments=deployments,
        policy_source=active.source,
    )
    if verdict.decision is RiskDecision.DENY:
        raise ExecutionConflictError(verdict.detail)


async def _require_entry_admission(
    risk_store: RiskPolicyStore | None,
    *,
    store: ExecutionStore,
    market_data: MarketDataService | None,
    request: DiscretionaryOrderRequest,
    snapshot: DeploymentSnapshot,
    notional: Decimal,
    quantity: Decimal,
    live_quote_cash: Decimal | None,
    deployments: tuple[Deployment, ...],
    entry_price: Decimal,
    reference_price: Decimal,
) -> None:
    """Fail closed when the registry rejects this sized entry."""
    active = await load_effective_policy(risk_store)
    peers = await _accounting_snapshots(
        store, deployments=deployments, exclude_id=snapshot.deployment.id
    )
    live_cash = live_quote_cash if request.mode is DeploymentMode.LIVE else None
    books = (*peers, snapshot)
    as_of = utc_now()
    beta = await load_entry_beta(
        active.definition,
        market_data,
        mode=request.mode,
        snapshots=books,
        product_id=request.product_id,
        as_of=as_of,
    )
    verdict = evaluate_new_entry(
        active.definition,
        mode=request.mode,
        proposed=ProposedEntry(
            product_id=request.product_id,
            strategy_id=None,
            notional=notional,
            quantity=quantity,
        ),
        snapshots=books,
        live_quote_cash=live_cash,
        observation=EntryObservation(
            as_of=as_of,
            proposed_price=entry_price,
            reference_price=reference_price,
            marks={request.product_id: reference_price},
        ),
        beta=beta,
    )
    if verdict.decision is RiskDecision.ALLOW:
        return
    await _pause_on_breaker(
        store=store,
        request=request,
        snapshot=snapshot,
        deployments=deployments,
        peers=peers,
        verdict=verdict,
    )
    raise ExecutionConflictError(verdict.detail)


async def _pause_on_breaker(
    *,
    store: ExecutionStore,
    request: DiscretionaryOrderRequest,
    snapshot: DeploymentSnapshot,
    deployments: tuple[Deployment, ...],
    peers: tuple[DeploymentSnapshot, ...],
    verdict: RiskVerdict,
) -> None:
    """Pause this book, or persisted same-quote mode peers on daily loss, with a latch."""
    if not pauses_risk_increasing(verdict.reason_code):
        return
    detail = f"{verdict.reason_code.value}: {verdict.detail}"
    if verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT:
        persisted = {item.id for item in deployments}
        await _pause_mode_running(
            store=store,
            mode=request.mode,
            product_id=request.product_id,
            portfolio=tuple(item for item in (*peers, snapshot) if item.deployment.id in persisted),
            detail=detail,
        )
        return
    if any(item.id == snapshot.deployment.id for item in deployments):
        await _pause(snapshot, store=store, detail=detail)


async def _accounting_snapshots(
    store: ExecutionStore,
    *,
    deployments: tuple[Deployment, ...],
    exclude_id: UUID,
) -> tuple[DeploymentSnapshot, ...]:
    """Load peer books that can still evidence UTC-day loss, including stopped flat rows."""
    peers: list[DeploymentSnapshot] = []
    for item in deployments:
        if item.id == exclude_id or not counts_for_daily_loss(item.status):
            continue
        peers.append(await accounting_snapshot(store, item.id, as_of=utc_now()))
    return tuple(peers)


def _require_matching_paper_fees(book: Deployment, request: DiscretionaryOrderRequest) -> None:
    """Refuse a second paper ticket that would silently change the book's fee assumptions."""
    if request.mode is not DeploymentMode.PAPER:
        return
    if request.paper_maker_fee_rate is None and request.paper_taker_fee_rate is None:
        return
    if (
        book.paper_maker_fee_rate != request.paper_maker_fee_rate
        or book.paper_taker_fee_rate != request.paper_taker_fee_rate
    ):
        raise ExecutionConflictError(
            "Paper fee rates are fixed on the existing discretionary book."
        )
