"""Circuit breakers and breaker pauses of the closed-bar execution loop.

Bar observation for breaker evaluation, runtime breaker application, the breaker
pause with the account-wide same-quote daily-loss peer latch, the fresh accounting
portfolio they read, and the post-exit re-entry cooldown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.capital import live_capital_base
from thytrader.execution.decision_scope import note_breaker
from thytrader.execution.runtime_ops import _pause
from thytrader.risk.accounting_evidence import accounting_portfolio
from thytrader.risk.breakers import EntryObservation, breaker_pause_detail, quote_scoped_snapshots
from thytrader.risk.gate import evaluate_runtime_breakers
from thytrader.risk.models import RiskDecision, RiskReasonCode, RiskVerdict, pauses_risk_increasing
from thytrader.trading.ids import utc_now
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionConflictError,
    ExecutionStoreError,
    with_runtime,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from decimal import Decimal
    from uuid import UUID

    from thytrader.market_data.models import Candle
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.store import ExecutionStore


def _exit_cooldown(strategy: StrategyDefinition | None, cooldown_bars: int | None) -> int:
    """Use an explicit cooldown, else the strategy's, else zero for discretionary books."""
    if cooldown_bars is not None:
        return cooldown_bars
    if strategy is None:
        return 0
    return strategy.entry.cooldown_bars


async def _portfolio_with_current(
    portfolio: Sequence[DeploymentSnapshot],
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
) -> tuple[DeploymentSnapshot, ...]:
    """Reload authoritative shared books, including sibling fills reconciled this cycle."""
    loaded = await accounting_portfolio(store, as_of=utc_now())
    expected = {item.deployment.id for item in portfolio} | {snapshot.deployment.id}
    if not expected.issubset({item.deployment.id for item in loaded}):
        raise ExecutionStoreError("Risk accounting inventory is incomplete.")
    return loaded


def _bar_observation(
    *,
    product_id: str,
    candle: Candle,
    proposed_price: Decimal | None,
    marks: Mapping[str, Decimal] | None,
) -> EntryObservation:
    """Build breaker observation from this bar's close plus any sibling marks."""
    combined = dict(marks) if marks is not None else {}
    combined[product_id] = candle.close
    return EntryObservation(
        as_of=utc_now(),
        proposed_price=proposed_price,
        reference_price=candle.close,
        marks=combined,
    )


async def _apply_circuit_breakers(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product_id: str,
    store: ExecutionStore,
    risk_policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
    marks: Mapping[str, Decimal] | None,
) -> DeploymentSnapshot:
    """Pause when daily-loss or drawdown has already tripped before a new entry."""
    try:
        current_portfolio = await _portfolio_with_current(portfolio, snapshot, store=store)
    except ExecutionStoreError:
        # Entry admission fails closed separately; protection must keep flowing.
        return snapshot
    current = next(
        item for item in current_portfolio if item.deployment.id == snapshot.deployment.id
    )
    live_cash = live_capital_base(current.deployment)
    verdict = evaluate_runtime_breakers(
        risk_policy,
        mode=snapshot.deployment.mode,
        snapshot=current,
        snapshots=current_portfolio,
        live_quote_cash=live_cash,
        observation=_bar_observation(
            product_id=product_id,
            candle=candle,
            proposed_price=None,
            marks=marks,
        ),
    )
    if verdict.decision is RiskDecision.ALLOW:
        return snapshot
    if not pauses_risk_increasing(verdict.reason_code):
        return snapshot
    note_breaker(verdict)
    return await _pause_for_breaker(snapshot, store=store, portfolio=portfolio, verdict=verdict)


async def _pause_for_breaker(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    portfolio: Sequence[DeploymentSnapshot],
    verdict: RiskVerdict,
) -> DeploymentSnapshot:
    """Pause this book, and same-quote books in the mode when daily loss trips."""
    # Admission may have observed sibling fills newer than the caller's runtime view.
    snapshot = await store.get_deployment(snapshot.deployment.id)
    detail = breaker_pause_detail(verdict.reason_code, verdict.detail)
    latched_daily = verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT
    latched_dd = verdict.reason_code is RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT
    if latched_daily or latched_dd:
        stamped = with_runtime(
            snapshot.deployment,
            updated_at=utc_now(),
            daily_loss_latched=True if latched_daily else None,
            drawdown_latched=True if latched_dd else None,
        )
        await store.save_deployment(stamped, expected_revision=snapshot.deployment.revision)
        snapshot = await store.get_deployment(snapshot.deployment.id)
    if verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT:
        await _pause_mode_running(
            store=store,
            mode=snapshot.deployment.mode,
            product_id=snapshot.deployment.product_id,
            portfolio=await _portfolio_with_current(portfolio, snapshot, store=store),
            detail=detail,
        )
        return await store.get_deployment(snapshot.deployment.id)
    return await _pause(snapshot, store=store, detail=detail)


async def _pause_mode_running(
    *,
    store: ExecutionStore,
    mode: DeploymentMode,
    product_id: str,
    portfolio: Sequence[DeploymentSnapshot],
    detail: str,
) -> None:
    """Pause running same-quote books, keeping an account latch on one retained row.

    Strategy callers already latch the triggering book. Discretionary admission may
    deny before its candidate exists, so keep the latch on one persisted peer instead.
    Stopped and deliberate pauses remain unchanged; exits continue. Explicit reset
    applies to the retained row carrying the latch, never implicitly to all books.
    """
    books, incomplete = quote_scoped_snapshots(
        tuple(item for item in portfolio if item.deployment.mode is mode), product_id
    )
    if incomplete is not None:
        # Unknown quote evidence already denies admission; do not pause unrelated quotes.
        return
    anchor = None
    if books and not any(item.deployment.daily_loss_latched for item in books):
        anchor = books[0].deployment.id
    for item in books:
        await _pause_daily_peer(
            store=store,
            deployment_id=item.deployment.id,
            mode=mode,
            product_id=product_id,
            detail=detail,
            latch=item.deployment.id == anchor,
        )


async def _pause_daily_peer(
    *,
    store: ExecutionStore,
    deployment_id: UUID,
    mode: DeploymentMode,
    product_id: str,
    detail: str,
    latch: bool,
) -> None:
    """Revalidate each peer and CAS only breaker metadata, retrying a bounded revision race."""
    for attempt in range(3):
        current = await store.get_accounting_snapshot(deployment_id)
        books, incomplete = quote_scoped_snapshots((current,), product_id)
        if current.deployment.mode is not mode or incomplete is not None or not books:
            return
        if current.deployment.status is not DeploymentStatus.RUNNING and (
            not latch or current.deployment.daily_loss_latched
        ):
            return
        try:
            await store.save_breaker_pause(
                deployment_id,
                expected_revision=current.deployment.revision,
                detail=detail,
                daily_loss_latched=latch,
            )
        except ExecutionConflictError:
            if attempt == 2:
                raise
        else:
            return
