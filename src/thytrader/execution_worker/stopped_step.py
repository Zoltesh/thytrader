"""Stopped strategy books in one execution-worker cycle.

Applies flatten or managed shutdown to every stopped book, journals its bars without
allowing entries, and keeps protection supervised when the strategy snapshot is gone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.stopped import supervise_stopped_deployment
from thytrader.execution_worker.bar_journal import _journaled_bar
from thytrader.execution_worker.windows import (
    _closed_reference_windows,
    _closed_window_for,
    _signal_exit_windows,
)
from thytrader.strategies.models import signal_exit_condition
from thytrader.trading.ids import utc_now
from thytrader.trading.models import DeploymentKind, DeploymentStatus, with_runtime

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from thytrader.execution.broker import Broker
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.strategies.snapshots import StrategySnapshotStore
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore


async def _process_stopped(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition | None,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
) -> None:
    """Apply flatten or managed-shutdown to every stopped book without dropping risk."""

    async def _signal_windows(
        strategy: StrategyDefinition, *, product_id: str, deploy_anchor: datetime
    ) -> tuple[tuple[Candle, ...], dict[str, tuple[Candle, ...]], dict[str, tuple[Candle, ...]]]:
        """Load one stopped product's signal-exit clocks, or nothing on a gap."""
        htf, extra = await _signal_exit_windows(
            market_data, strategy, product_id=product_id, deploy_anchor=deploy_anchor
        )
        references = (
            {}
            if signal_exit_condition(strategy.exits) is None
            else await _closed_reference_windows(market_data, strategy, deploy_anchor=deploy_anchor)
        )
        return htf, extra, references

    await supervise_stopped_deployment(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=market_data,
        paper_broker=paper_broker,
        live_broker=live_broker,
        load_closed_window=_closed_window_for,
        load_signal_windows=_signal_windows,
        journal_strategy_bar=_journal_stopped_bar,
    )


async def _journal_stopped_bar(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    candles: tuple[Candle, ...],
    advance: Callable[[], Awaitable[DeploymentSnapshot]],
    require_activity: bool,
) -> None:
    """Journal one stopped strategy bar without allowing a new entry."""
    await _journaled_bar(
        snapshot,
        strategy=strategy,
        product_id=product.product_id,
        candle=candles[-1],
        allow_new_entries=False,
        require_activity=require_activity,
        advance=advance,
    )


async def _stopped_strategy_definition(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    publication_store: StrategySnapshotStore,
) -> StrategyDefinition | None:
    """Keep shutdown supervision alive when immutable strategy rules cannot be loaded."""
    if snapshot.deployment.kind is DeploymentKind.DISCRETIONARY:
        return None
    fingerprint = snapshot.deployment.strategy_fingerprint
    if fingerprint is not None:
        try:
            return (await publication_store.load(fingerprint)).definition
        except RuntimeError, OSError, ValueError, TypeError:
            pass
    if snapshot.deployment.mismatch_detail is None:
        await store.save_deployment(
            with_runtime(
                snapshot.deployment,
                updated_at=utc_now(),
                status=DeploymentStatus.STOPPED,
                mismatch_detail=(
                    "Stopped strategy snapshot is unavailable; "
                    "only stored protection is maintained."
                ),
            )
        )
    return None
