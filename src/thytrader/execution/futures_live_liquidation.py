"""Live futures margin monitor: independent of bars, arming and entry policy switches."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, DecimalException, Inexact, localcontext
from typing import TYPE_CHECKING

from thytrader.execution.futures_reduction import reduce_live_futures_position
from thytrader.execution.runtime_ops import _pause
from thytrader.trading.futures_book import futures_book_equity
from thytrader.trading.models import IntentPurpose, PositionSide, with_runtime

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.execution.broker import Broker
    from thytrader.execution.futures_live import FuturesLiveAccount
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.futures_book import FuturesBookState
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore

_PENDING = "FUTURES_DERISK_PENDING: "


async def supervise_live_futures(
    snapshot: DeploymentSnapshot,
    *,
    state: FuturesBookState | None,
    account: FuturesLiveAccount | None,
    policy: RiskPolicyDefinition,
    broker: Broker,
    store: ExecutionStore,
    now: datetime,
) -> DeploymentSnapshot:
    """Continue durable de-risking even if the ratio recovers while cancellation is pending."""
    if snapshot.position is None:
        return snapshot
    pending = snapshot.deployment.futures_derisk_pending
    if not pending and not live_liquidation_due(
        snapshot, state=state, account=account, policy=policy, now=now
    ):
        return snapshot
    if not pending:
        await store.save_deployment(
            replace(
                snapshot.deployment,
                updated_at=now,
                futures_derisk_pending=True,
                mismatch_detail=_PENDING + "margin breach; cancel protection before reduction.",
            )
        )
        snapshot = await store.get_deployment(snapshot.deployment.id)
    reduced = await reduce_live_futures_position(
        snapshot,
        account=account,
        broker=broker,
        store=store,
        purpose=IntentPurpose.LIQUIDATION,
        now=now,
    )
    if reduced.position is None:
        return await _pause(
            reduced,
            store=store,
            detail="FUTURES_DERISK_COMPLETED: reduction filled; review before resuming entries.",
        )
    if reduced.position is not None:
        detail = reduced.deployment.mismatch_detail or "reduction still pending"
        if not detail.startswith(_PENDING):
            await store.save_deployment(
                with_runtime(reduced.deployment, updated_at=now, mismatch_detail=_PENDING + detail)
            )
            return await store.get_deployment(snapshot.deployment.id)
    return reduced


def live_liquidation_due(
    snapshot: DeploymentSnapshot,
    *,
    state: FuturesBookState | None,
    account: FuturesLiveAccount | None,
    policy: RiskPolicyDefinition,
    now: datetime,
) -> bool:
    """Use exact threshold multiplication, not a rounded display ratio; unknown is not low."""
    if account is None or not account.venue.is_fresh(now) or snapshot.position is None:
        return False
    futures = policy.futures
    ratio = None if futures is None else futures.live_derisk_margin_ratio
    if ratio is not None and _ratio_breached(account, Decimal(ratio)):
        return True
    mark = account.marks.get(snapshot.deployment.product_id)
    if mark is None or state is None or state.margin is None:
        return False
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            equity = futures_book_equity(snapshot, {snapshot.deployment.product_id: mark})
            side = "short" if snapshot.position.side is PositionSide.SHORT else "long"
            maintenance = state.margin.maintenance(snapshot.position.quantity, mark, side)
            return (
                equity is not None
                and maintenance.is_finite()
                and maintenance > 0
                and equity < maintenance
            )
    except DecimalException:
        return False


def _ratio_breached(account: FuturesLiveAccount, ratio: Decimal) -> bool:
    """A finite strictly positive venue liquidation threshold is required."""
    available = account.available_margin_usd
    threshold = account.liquidation_threshold_usd
    if (
        available is None
        or threshold is None
        or not available.is_finite()
        or not threshold.is_finite()
        or threshold <= 0
    ):
        return False
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            required = threshold * ratio
            return required.is_finite() and available < required
    except DecimalException:
        return False
