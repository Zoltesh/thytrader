"""Per-book live futures supervision, ahead of candle and entry gates."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from thytrader.execution.futures_live import (
    live_futures_account_scope,
    managed_live_futures,
    prepare_live_futures_book,
)
from thytrader.execution.futures_live_liquidation import supervise_live_futures
from thytrader.execution.futures_obligations import retain_flat_confirmation
from thytrader.execution.futures_position_supervision import enforce_position_check
from thytrader.execution.futures_recovery import recover_futures_intents
from thytrader.execution.futures_reduction import reduce_live_futures_position
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.stopped import _note_broker_unavailable
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.trading.ids import utc_now
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentStatus,
    IntentPurpose,
    LifecycleCommand,
    with_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from thytrader.execution.broker import Broker
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.futures_book import FuturesBookState
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore


LIVE_FUTURES_BROKER_UNAVAILABLE = (
    "Live broker is unavailable: no futures broker is composed, so this live futures book is not "
    "supervised (I9, de-risk, flatten) until it is."
)


@asynccontextmanager
async def supervised_live_futures(
    snapshot: DeploymentSnapshot,
    *,
    state: FuturesBookState | None,
    broker: Broker | None,
    store: ExecutionStore,
    policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
) -> AsyncIterator[DeploymentSnapshot]:
    """Keep live evidence scoped to the book and monitor even a stopped/no-candle book."""
    if (
        snapshot.deployment.mode is not DeploymentMode.LIVE
        or not is_futures_product_id(snapshot.deployment.product_id)
        or not managed_live_futures(snapshot)
    ):
        yield snapshot
        return
    if broker is None:
        yield await _without_futures_broker(snapshot, store=store)
        return
    snapshot = await retain_flat_confirmation(snapshot, store=store, now=utc_now())
    snapshot = await recover_futures_intents(snapshot, store=store)
    snapshot = await reconcile_open_orders(snapshot, broker=broker, store=store)
    account = await prepare_live_futures_book(snapshot, portfolio=portfolio)
    with live_futures_account_scope(account):
        snapshot = await enforce_position_check(
            snapshot,
            account=account,
            broker=broker,
            store=store,
            now=utc_now(),
        )
        snapshot = await supervise_live_futures(
            snapshot,
            state=state,
            account=account,
            policy=policy,
            broker=broker,
            store=store,
            now=utc_now(),
        )
        if snapshot.deployment.lifecycle_command is LifecycleCommand.FLATTEN:
            snapshot = await reduce_live_futures_position(
                snapshot,
                account=account,
                broker=broker,
                store=store,
                purpose=IntentPurpose.STOP,
                now=utc_now(),
            )
        yield snapshot


async def _without_futures_broker(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore
) -> DeploymentSnapshot:
    """No supervision is possible: pause a running book, note a stopped one, keep every duty.

    Deferred duties (de-risk, flat confirmation, an I9 pause) would otherwise skip both this
    supervision and the ordinary missing-broker pause, leaving the book silently unsupervised.
    """
    deployment = snapshot.deployment
    if deployment.status is DeploymentStatus.STOPPED:
        await _note_broker_unavailable(snapshot, store=store)
    elif deployment.status is DeploymentStatus.RUNNING:
        await store.save_deployment(
            with_runtime(
                deployment,
                updated_at=utc_now(),
                status=DeploymentStatus.PAUSED,
                mismatch_detail=LIVE_FUTURES_BROKER_UNAVAILABLE,
            )
        )
    else:
        return snapshot
    return await store.get_deployment(deployment.id)
