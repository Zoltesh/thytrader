"""Continuously evaluate deployed strategies against closed candles."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Protocol

from thytrader.exchanges.ws.market_feed import DEFAULT_HEARTBEAT_TIMEOUT_SECONDS
from thytrader.execution.discretionary import process_discretionary_bar
from thytrader.execution.geometry import base_currency
from thytrader.execution.ids import utc_now
from thytrader.execution.loop import (
    cancel_resting_orders,
    maintain_open_inventory,
    process_closed_bar,
)
from thytrader.execution.models import (
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    with_runtime,
)
from thytrader.execution.overlay import InstrumentScopedStore
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.trade_reason_scope import (
    discretionary_trade_reason_scope,
    strategy_trade_reason_scope,
    trade_reason_scope,
)
from thytrader.execution.user_feed_state import UserOrderFeedState, UserOrderFeedUnavailableError
from thytrader.market_data.models import parse_candle_interval
from thytrader.risk.store import load_effective_policy
from thytrader.strategies.models import (
    extra_indicator_timeframe_groups,
    extra_indicator_timeframe_warmup,
    lockstep_product_ids,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from decimal import Decimal
    from uuid import UUID

    from thytrader.exchanges.models import ExchangeBalance
    from thytrader.execution.broker import Broker
    from thytrader.execution.models import Deployment, DeploymentSnapshot
    from thytrader.execution.store import ExecutionStore
    from thytrader.execution.user_feed_state import UserOrderFeedStateStore
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.memory.store import ExperientialMemoryStore
    from thytrader.persistence.worker_heartbeats import WorkerHeartbeatStore
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.settings_yaml import SettingsStore
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.strategies.publication import StrategyPublicationStore

_logger = logging.getLogger(__name__)


class QuoteBalanceReader(Protocol):
    """Read quote cash for live sizing."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Return non-empty exchange balances."""
        ...


async def run_execution_worker(
    stop_requested: asyncio.Event,
    *,
    store: ExecutionStore,
    publication_store: StrategyPublicationStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    interval_seconds: int,
    on_readiness_changed: Callable[[bool], None] | None = None,
    heartbeat_store: WorkerHeartbeatStore | None = None,
    risk_store: RiskPolicyStore | None = None,
    user_feed_store: UserOrderFeedStateStore | None = None,
    wake_requested: asyncio.Event | None = None,
    memory_store: ExperientialMemoryStore | None = None,
    settings_store: SettingsStore | None = None,
) -> None:
    """Poll running deployments until shutdown."""
    if on_readiness_changed is not None:
        on_readiness_changed(True)
    try:
        while not stop_requested.is_set():
            if heartbeat_store is not None:
                await heartbeat_store.touch("execution_worker", datetime.now(UTC))
            await _run_cycle(
                store=store,
                publication_store=publication_store,
                market_data=market_data,
                paper_broker=paper_broker,
                live_broker=live_broker,
                quote_reader=quote_reader,
                risk_store=risk_store,
                user_feed_store=user_feed_store,
                memory_store=memory_store,
            )
            if wake_requested is not None:
                wake_requested.clear()
            wait_seconds = (
                settings_store.current().execution_worker_interval_seconds
                if settings_store is not None
                else interval_seconds
            )
            await _await_next_cycle(
                stop_requested, wake_requested=wake_requested, interval_seconds=wait_seconds
            )
    finally:
        if on_readiness_changed is not None:
            on_readiness_changed(False)


async def _await_next_cycle(
    stop_requested: asyncio.Event,
    *,
    wake_requested: asyncio.Event | None,
    interval_seconds: int,
) -> None:
    """Sleep until the poll interval, a user-feed nudge, or shutdown."""
    if wake_requested is None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_requested.wait(), timeout=interval_seconds)
        return
    wake = asyncio.create_task(wake_requested.wait())
    stop = asyncio.create_task(stop_requested.wait())
    _done, pending = await asyncio.wait(
        {wake, stop}, timeout=interval_seconds, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()


async def _run_cycle(
    *,
    store: ExecutionStore,
    publication_store: StrategyPublicationStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    risk_store: RiskPolicyStore | None,
    user_feed_store: UserOrderFeedStateStore | None = None,
    memory_store: ExperientialMemoryStore | None = None,
) -> None:
    """Process occupied deployments once, refreshing occupancy after each for the entry gate."""
    policy = (await load_effective_policy(risk_store)).definition
    deployments = await store.list_deployments()
    portfolio = await _occupied_snapshots(store, deployments)
    for deployment in deployments:
        if deployment.status is DeploymentStatus.STOPPED:
            try:
                await _cancel_stopped(
                    deployment_id=deployment.id,
                    store=store,
                    paper_broker=paper_broker,
                    live_broker=live_broker,
                )
            except RuntimeError, ValueError, TypeError, OSError:
                _logger.exception("execution_cancel_failed deployment_id=%s", deployment.id)
            continue
        if deployment.status not in {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}:
            continue
        try:
            await _process_one(
                deployment_id=deployment.id,
                store=store,
                publication_store=publication_store,
                market_data=market_data,
                paper_broker=paper_broker,
                live_broker=live_broker,
                quote_reader=quote_reader,
                risk_policy=policy,
                portfolio=portfolio,
                user_feed_store=user_feed_store,
                memory_store=memory_store,
            )
        except RuntimeError, ValueError, TypeError, OSError:
            _logger.exception("execution_cycle_failed deployment_id=%s", deployment.id)
        portfolio = await _occupied_snapshots(store, deployments)


async def _cancel_stopped(
    *,
    deployment_id: UUID,
    store: ExecutionStore,
    paper_broker: Broker,
    live_broker: Broker | None,
) -> None:
    """Cancel resting orders on a permanently stopped deployment."""
    snapshot = await store.get_deployment(deployment_id)
    broker = paper_broker
    if snapshot.deployment.mode is DeploymentMode.LIVE:
        if live_broker is None:
            return
        broker = live_broker
    await cancel_resting_orders(snapshot, broker=broker, store=store)


async def _process_one(
    *,
    deployment_id: UUID,
    store: ExecutionStore,
    publication_store: StrategyPublicationStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    risk_policy: RiskPolicyDefinition,
    portfolio: tuple[DeploymentSnapshot, ...],
    user_feed_store: UserOrderFeedStateStore | None,
    memory_store: ExperientialMemoryStore | None,
) -> None:
    """Load evidence and advance one deployment through newly closed bars."""
    snapshot = await store.get_deployment(deployment_id)
    if snapshot.deployment.kind is DeploymentKind.DISCRETIONARY:
        await _process_discretionary(
            snapshot,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
            user_feed_store=user_feed_store,
            risk_policy=risk_policy,
            memory_store=memory_store,
        )
        return
    strategy = await _strategy_definition(
        snapshot, store=store, publication_store=publication_store
    )
    if strategy is None:
        return
    await _advance_strategy(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=market_data,
        paper_broker=paper_broker,
        live_broker=live_broker,
        quote_reader=quote_reader,
        risk_policy=risk_policy,
        portfolio=portfolio,
        user_feed_store=user_feed_store,
        memory_store=memory_store,
    )


async def _advance_strategy(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    risk_policy: RiskPolicyDefinition,
    portfolio: tuple[DeploymentSnapshot, ...],
    user_feed_store: UserOrderFeedStateStore | None,
    memory_store: ExperientialMemoryStore | None,
) -> None:
    """Advance one published-strategy deployment through newly closed bars."""
    deployment = snapshot.deployment
    if await _pause_five_minute_live_if_feed_down(
        snapshot, timeframe=strategy.timeframe, store=store, user_feed_store=user_feed_store
    ):
        return
    product, candles, expected_last = await _closed_window(market_data, strategy)
    if not candles:
        return
    interval = parse_candle_interval(strategy.timeframe)
    due = new_closed_bars(
        candles,
        last_evaluated_bar=deployment.last_evaluated_bar,
        expected_last_start=expected_last,
        bar_duration=interval.duration,
    )
    if due is None:
        paused = with_runtime(
            deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Market-data window is gapped or missing the latest closed bar.",
        )
        await store.save_deployment(paused)
        return
    if not due:
        await _maintain_between_bars(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
            product=product,
            candles=candles,
        )
        return
    covered = lockstep_product_ids(strategy)
    if len(covered) > 1:
        await _advance_multi_instrument(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
            risk_policy=risk_policy,
            portfolio=portfolio,
            primary_product=product,
            primary_candles=candles,
            due=due,
            memory_store=memory_store,
        )
        return
    htf_candles = await _closed_htf_window(market_data, strategy)
    if htf_candles is None:
        paused = with_runtime(
            deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail=(
                "HTF market-data window is gapped or missing the latest completed HTF bar."
            ),
        )
        await store.save_deployment(paused)
        return
    await _evaluate_strategy_due_bars(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=market_data,
        paper_broker=paper_broker,
        live_broker=live_broker,
        quote_reader=quote_reader,
        risk_policy=risk_policy,
        portfolio=portfolio,
        product=product,
        candles=candles,
        due=due,
        htf_candles=htf_candles,
        memory_store=memory_store,
    )


async def _advance_multi_instrument(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    risk_policy: RiskPolicyDefinition,
    portfolio: tuple[DeploymentSnapshot, ...],
    primary_product: MarketProduct,
    primary_candles: Sequence[Candle],
    due: Sequence[Candle],
    memory_store: ExperientialMemoryStore | None,
) -> None:
    """Evaluate covered products in lexicographic order on each shared closed bar."""
    covered = lockstep_product_ids(strategy)
    windows = await _load_lockstep_product_windows(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=market_data,
        covered=covered,
        primary_product=primary_product,
        primary_candles=primary_candles,
    )
    if windows is None:
        return
    overlays = await _load_lockstep_filter_windows(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=market_data,
        covered=covered,
    )
    if overlays is None:
        return
    htf_by_product, extra_by_product = overlays
    deployment = snapshot.deployment
    broker: Broker = paper_broker
    if deployment.mode is DeploymentMode.LIVE:
        prepared = await _prepare_live(
            snapshot,
            store=store,
            live_broker=live_broker,
            quote_reader=quote_reader,
            quote_currency=strategy.instrument.quote_currency,
            product_id=primary_product.product_id,
            cooldown_bars=strategy.entry.cooldown_bars,
        )
        if prepared is None or live_broker is None:
            return
        snapshot = prepared
        broker = live_broker
    if not due:
        await _maintain_multi_between_bars(
            snapshot,
            strategy=strategy,
            store=store,
            covered=covered,
            windows=windows,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
        )
        return
    for candle in due:
        stopped = await _evaluate_lockstep_bar(
            candle,
            covered=covered,
            windows=windows,
            htf_by_product=htf_by_product,
            extra_by_product=extra_by_product,
            deployment_id=deployment.id,
            strategy=strategy,
            store=store,
            market_data=market_data,
            broker=broker,
            quote_reader=quote_reader,
            risk_policy=risk_policy,
            portfolio=portfolio,
            memory_store=memory_store,
        )
        if stopped:
            return


async def _load_lockstep_product_windows(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    covered: tuple[str, ...],
    primary_product: MarketProduct,
    primary_candles: Sequence[Candle],
) -> dict[str, tuple[MarketProduct, tuple[Candle, ...]]] | None:
    """Load closed LTF windows for every covered product, or pause on a gap."""
    windows: dict[str, tuple[MarketProduct, tuple[Candle, ...]]] = {
        primary_product.product_id: (primary_product, tuple(primary_candles))
    }
    interval = parse_candle_interval(strategy.timeframe)
    for product_id in covered:
        if product_id in windows:
            continue
        extra_product, extra_candles, extra_expected = await _closed_window_for(
            market_data,
            product_id=product_id,
            timeframe=strategy.timeframe,
            warmup_bars=strategy.data_requirements.warmup_bars,
        )
        extra_due = new_closed_bars(
            extra_candles,
            last_evaluated_bar=snapshot.deployment.last_evaluated_bar,
            expected_last_start=extra_expected,
            bar_duration=interval.duration,
        )
        if extra_due is None:
            await _pause_coverage_gap(snapshot, store=store, product_id=product_id)
            return None
        windows[product_id] = (extra_product, extra_candles)
    return windows


async def _load_lockstep_filter_windows(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    covered: tuple[str, ...],
) -> tuple[dict[str, tuple[Candle, ...]], dict[str, dict[str, tuple[Candle, ...]]]] | None:
    """Load last-completed HTF and extra-TF windows, or pause on a gap."""
    htf_by_product: dict[str, tuple[Candle, ...]] = {}
    extra_by_product: dict[str, dict[str, tuple[Candle, ...]]] = {}
    for product_id in covered:
        htf_candles = await _closed_htf_window(market_data, strategy, product_id=product_id)
        if htf_candles is None:
            paused = with_runtime(
                snapshot.deployment,
                updated_at=utc_now(),
                status=DeploymentStatus.PAUSED,
                mismatch_detail=(
                    "HTF market-data window is gapped or missing the latest completed HTF bar "
                    f"on {product_id}."
                ),
            )
            await store.save_deployment(paused)
            return None
        extra_candles = await _closed_indicator_timeframe_windows(
            market_data, strategy, htf_candles, product_id=product_id
        )
        if extra_candles is None:
            paused = with_runtime(
                snapshot.deployment,
                updated_at=utc_now(),
                status=DeploymentStatus.PAUSED,
                mismatch_detail=(
                    "Indicator-timeframe market-data window is gapped or missing the latest "
                    f"completed bar on {product_id}."
                ),
            )
            await store.save_deployment(paused)
            return None
        htf_by_product[product_id] = htf_candles
        extra_by_product[product_id] = extra_candles
    return htf_by_product, extra_by_product


async def _evaluate_lockstep_bar(
    candle: Candle,
    *,
    covered: tuple[str, ...],
    windows: dict[str, tuple[MarketProduct, tuple[Candle, ...]]],
    htf_by_product: dict[str, tuple[Candle, ...]],
    extra_by_product: dict[str, dict[str, tuple[Candle, ...]]],
    deployment_id: UUID,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    broker: Broker,
    quote_reader: QuoteBalanceReader | None,
    risk_policy: RiskPolicyDefinition,
    portfolio: tuple[DeploymentSnapshot, ...],
    memory_store: ExperientialMemoryStore | None,
) -> bool:
    """Evaluate every covered product on one shared closed bar. True if the loop should stop."""
    current = await store.get_deployment(deployment_id)
    if current.deployment.status is DeploymentStatus.STOPPED:
        return True
    marks: dict[str, Decimal] = {}
    product_bars: dict[str, tuple[MarketProduct, tuple[Candle, ...], Candle]] = {}
    for product_id in covered:
        product, candles = windows[product_id]
        bar = next((item for item in candles if item.starts_at == candle.starts_at), None)
        if bar is None:
            await _pause_coverage_gap(current, store=store, product_id=product_id)
            return True
        window = tuple(item for item in candles if item.starts_at <= candle.starts_at)
        product_bars[product_id] = (product, window, bar)
        marks[product_id] = bar.close
    peer_marks = await _portfolio_marks(
        market_data,
        portfolio=portfolio,
        fallback_timeframe=strategy.timeframe,
        current_product_id=covered[0],
        current_close=marks[covered[0]],
    )
    marks.update(peer_marks)
    for product_id in covered:
        product, window, bar = product_bars[product_id]
        scoped = InstrumentScopedStore(store, product_id)
        focused = await scoped.get_deployment(deployment_id)
        live_base_available = None
        if focused.deployment.mode is DeploymentMode.LIVE and quote_reader is not None:
            live_base_available = await _currency_available(
                quote_reader, base_currency(product.product_id)
            )
        with trade_reason_scope(
            strategy_trade_reason_scope(
                memory_store,
                deployment=focused.deployment,
                strategy=strategy,
                policy=risk_policy,
            )
        ):
            await process_closed_bar(
                focused,
                strategy=strategy,
                product=product,
                candles=window,
                broker=broker,
                store=scoped,
                risk_policy=risk_policy,
                portfolio=portfolio,
                htf_candles=htf_by_product[product_id],
                indicator_timeframe_candles=extra_by_product[product_id],
                live_base_available=live_base_available,
                marks=marks,
            )
        latest = await store.get_deployment(deployment_id)
        if latest.deployment.status is DeploymentStatus.STOPPED:
            return True
    parent = await store.get_deployment(deployment_id)
    await store.save_deployment(
        with_runtime(
            parent.deployment,
            updated_at=utc_now(),
            last_evaluated_bar=candle.starts_at,
        )
    )
    return False


async def _pause_coverage_gap(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore, product_id: str
) -> None:
    """Pause when any covered product is missing the shared closed bar."""
    paused = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        status=DeploymentStatus.PAUSED,
        mismatch_detail=(
            f"Market-data window is gapped or missing the latest closed bar on {product_id}."
        ),
    )
    await store.save_deployment(paused)


async def _evaluate_strategy_due_bars(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    risk_policy: RiskPolicyDefinition,
    portfolio: tuple[DeploymentSnapshot, ...],
    product: MarketProduct,
    candles: Sequence[Candle],
    due: Sequence[Candle],
    htf_candles: Sequence[Candle],
    memory_store: ExperientialMemoryStore | None,
) -> None:
    """Compose extra-TF windows with the shipped closed-bar HTF evaluation path."""
    extra_candles = await _indicator_timeframe_windows_or_pause(
        snapshot, strategy=strategy, store=store, market_data=market_data, htf_candles=htf_candles
    )
    if extra_candles is None:
        return
    deployment = snapshot.deployment
    broker: Broker = paper_broker
    if deployment.mode is DeploymentMode.LIVE:
        prepared = await _prepare_live(
            snapshot,
            store=store,
            live_broker=live_broker,
            quote_reader=quote_reader,
            quote_currency=strategy.instrument.quote_currency,
            product_id=product.product_id,
            cooldown_bars=strategy.entry.cooldown_bars,
        )
        if prepared is None or live_broker is None:
            return
        snapshot = prepared
        broker = live_broker
    for candle in due:
        current = await store.get_deployment(deployment.id)
        if current.deployment.status is DeploymentStatus.STOPPED:
            return
        window = tuple(item for item in candles if item.starts_at <= candle.starts_at)
        live_base_available = None
        if current.deployment.mode is DeploymentMode.LIVE and quote_reader is not None:
            live_base_available = await _currency_available(
                quote_reader, base_currency(product.product_id)
            )
        marks = await _portfolio_marks(
            market_data,
            portfolio=portfolio,
            fallback_timeframe=strategy.timeframe,
            current_product_id=product.product_id,
            current_close=candle.close,
        )
        with trade_reason_scope(
            strategy_trade_reason_scope(
                memory_store,
                deployment=current.deployment,
                strategy=strategy,
                policy=risk_policy,
            )
        ):
            await process_closed_bar(
                current,
                strategy=strategy,
                product=product,
                candles=window,
                broker=broker,
                store=store,
                risk_policy=risk_policy,
                portfolio=portfolio,
                htf_candles=htf_candles,
                indicator_timeframe_candles=extra_candles,
                live_base_available=live_base_available,
                marks=marks,
            )


async def _strategy_definition(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    publication_store: StrategyPublicationStore,
) -> StrategyDefinition | None:
    """Load the published strategy, or pause when identity is missing."""
    fingerprint = snapshot.deployment.strategy_fingerprint
    if fingerprint is None:
        paused = with_runtime(
            snapshot.deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Strategy deployment is missing published identity.",
        )
        await store.save_deployment(paused)
        return None
    published = await publication_store.load(fingerprint)
    return published.definition


async def _process_discretionary(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    user_feed_store: UserOrderFeedStateStore | None,
    risk_policy: RiskPolicyDefinition,
    memory_store: ExperientialMemoryStore | None,
) -> None:
    """Reconcile and protect a discretionary book without strategy signal evaluation."""
    deployment = snapshot.deployment
    timeframe = deployment.timeframe or "1h"
    if await _pause_five_minute_live_if_feed_down(
        snapshot, timeframe=timeframe, store=store, user_feed_store=user_feed_store
    ):
        return
    product, candles, expected_last = await _closed_window_for(
        market_data,
        product_id=deployment.product_id,
        timeframe=timeframe,
        warmup_bars=3,
    )
    if not candles:
        return
    interval = parse_candle_interval(timeframe)
    due = new_closed_bars(
        candles,
        last_evaluated_bar=deployment.last_evaluated_bar,
        expected_last_start=expected_last,
        bar_duration=interval.duration,
    )
    if due is None:
        paused = with_runtime(
            deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Market-data window is gapped or missing the latest closed bar.",
        )
        await store.save_deployment(paused)
        return
    broker: Broker = paper_broker
    if deployment.mode is DeploymentMode.LIVE:
        prepared = await _prepare_live(
            snapshot,
            store=store,
            live_broker=live_broker,
            quote_reader=quote_reader,
            quote_currency="USD",
            product_id=product.product_id,
            cooldown_bars=0,
        )
        if prepared is None or live_broker is None:
            return
        snapshot = prepared
        broker = live_broker
        paused_with_mismatch = (
            snapshot.deployment.status is DeploymentStatus.PAUSED
            and snapshot.deployment.mismatch_detail
        )
        if paused_with_mismatch:
            return
    for candle in due:
        current = await store.get_deployment(deployment.id)
        if current.deployment.status is DeploymentStatus.STOPPED:
            return
        window = tuple(item for item in candles if item.starts_at <= candle.starts_at)
        with trade_reason_scope(
            discretionary_trade_reason_scope(
                memory_store,
                policy=risk_policy,
                timeframe=timeframe,
                note=None,
                note_origin=None,
            )
        ):
            await process_discretionary_bar(
                current,
                product=product,
                candles=window,
                broker=broker,
                store=store,
            )


async def _maintain_between_bars(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    product: MarketProduct,
    candles: Sequence[Candle],
) -> None:
    """Reconcile and ensure protection when no newly closed bar is due."""
    del market_data
    broker: Broker = paper_broker
    if snapshot.deployment.mode is DeploymentMode.LIVE:
        prepared = await _prepare_live(
            snapshot,
            store=store,
            live_broker=live_broker,
            quote_reader=quote_reader,
            quote_currency=strategy.instrument.quote_currency,
            product_id=product.product_id,
            cooldown_bars=strategy.entry.cooldown_bars,
        )
        if prepared is None or live_broker is None:
            return
        snapshot = prepared
        broker = live_broker
    await maintain_open_inventory(
        snapshot,
        strategy=strategy,
        product=product,
        candles=candles,
        broker=broker,
        store=store,
    )


async def _maintain_multi_between_bars(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    covered: tuple[str, ...],
    windows: dict[str, tuple[MarketProduct, tuple[Candle, ...]]],
    paper_broker: Broker,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
) -> None:
    """Reconcile and ensure protection for every covered product between bars."""
    broker: Broker = paper_broker
    if snapshot.deployment.mode is DeploymentMode.LIVE:
        prepared = await _prepare_live(
            snapshot,
            store=store,
            live_broker=live_broker,
            quote_reader=quote_reader,
            quote_currency=strategy.instrument.quote_currency,
            product_id=snapshot.deployment.product_id,
            cooldown_bars=strategy.entry.cooldown_bars,
        )
        if prepared is None or live_broker is None:
            return
        snapshot = prepared
        broker = live_broker
    for product_id in covered:
        product, candles = windows[product_id]
        if not candles:
            continue
        scoped = InstrumentScopedStore(store, product_id)
        focused = await scoped.get_deployment(snapshot.deployment.id)
        await maintain_open_inventory(
            focused,
            strategy=strategy,
            product=product,
            candles=candles,
            broker=broker,
            store=scoped,
        )


async def _prepare_live(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    quote_currency: str,
    product_id: str,
    cooldown_bars: int,
) -> DeploymentSnapshot | None:
    """Pause without a live broker, else reconcile fills then refresh quote cash."""
    deployment = snapshot.deployment
    if live_broker is None:
        paused = with_runtime(
            deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Live broker is unavailable.",
        )
        await store.save_deployment(paused)
        return None
    snapshot = await reconcile_open_orders(
        snapshot,
        broker=live_broker,
        store=store,
        product_id=product_id,
        cooldown_bars=cooldown_bars,
    )
    if snapshot.deployment.status is DeploymentStatus.PAUSED:
        return snapshot
    if quote_reader is not None:
        cash = await _currency_available(quote_reader, quote_currency)
        if cash is not None:
            current = await store.get_deployment(deployment.id)
            await store.save_deployment(
                with_runtime(current.deployment, updated_at=utc_now(), cash=cash)
            )
            snapshot = await store.get_deployment(deployment.id)
    return snapshot


def new_closed_bars(
    candles: Sequence[Candle],
    *,
    last_evaluated_bar: datetime | None,
    expected_last_start: datetime,
    bar_duration: timedelta,
) -> tuple[Candle, ...] | None:
    """Return newly closed bars in order, or None when the window is gapped or stale."""
    if not candles or not _contiguous(candles, bar_duration):
        return None
    latest = candles[-1]
    if latest.starts_at != expected_last_start:
        return None
    if last_evaluated_bar is None:
        return (latest,)
    due = tuple(candle for candle in candles if candle.starts_at > last_evaluated_bar)
    expected = last_evaluated_bar + bar_duration
    for candle in due:
        if candle.starts_at != expected:
            return None
        expected = candle.starts_at + bar_duration
    return due


def _contiguous(candles: Sequence[Candle], bar_duration: timedelta) -> bool:
    """Return whether candle starts are consecutive closed bars of one interval."""
    previous: datetime | None = None
    for candle in candles:
        if previous is not None and candle.starts_at - previous != bar_duration:
            return False
        previous = candle.starts_at
    return True


def htf_coverage_ready(
    candles: Sequence[Candle],
    *,
    expected_last_start: datetime,
    bar_duration: timedelta,
) -> bool:
    """True when HTF bars are contiguous and include the latest completed HTF bar."""
    if not candles or not _contiguous(candles, bar_duration):
        return False
    return candles[-1].starts_at == expected_last_start


async def _closed_htf_window(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
    *,
    product_id: str | None = None,
) -> tuple[Candle, ...] | None:
    """Fetch complete-only last-completed HTF bars, or None when gapped."""
    htf_filter = strategy.htf_filter
    if htf_filter is None:
        return ()
    _product, candles, expected_last = await _closed_window_for(
        market_data,
        product_id=product_id or strategy.instrument.product_id,
        timeframe=htf_filter.timeframe,
        warmup_bars=htf_filter.data_requirements.warmup_bars,
    )
    interval = parse_candle_interval(htf_filter.timeframe)
    if not htf_coverage_ready(
        candles,
        expected_last_start=expected_last,
        bar_duration=interval.duration,
    ):
        return None
    return candles


async def _indicator_timeframe_windows_or_pause(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    htf_candles: Sequence[Candle],
) -> dict[str, tuple[Candle, ...]] | None:
    """Return extra-TF windows, or pause when that complete-only coverage is missing."""
    extra_candles = await _closed_indicator_timeframe_windows(market_data, strategy, htf_candles)
    if extra_candles is not None:
        return extra_candles
    paused = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        status=DeploymentStatus.PAUSED,
        mismatch_detail=(
            "Indicator-timeframe market-data window is gapped or missing the latest completed bar."
        ),
    )
    await store.save_deployment(paused)
    return None


async def _closed_indicator_timeframe_windows(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
    htf_candles: Sequence[Candle],
    *,
    product_id: str | None = None,
) -> dict[str, tuple[Candle, ...]] | None:
    """Fetch complete-only extra-TF bars, reusing the HTF window when clocks match."""
    windows: dict[str, tuple[Candle, ...]] = {}
    htf_timeframe = strategy.htf_filter.timeframe if strategy.htf_filter is not None else None
    covered_product = product_id or strategy.instrument.product_id
    for timeframe, indicators in extra_indicator_timeframe_groups(strategy):
        if timeframe == htf_timeframe:
            windows[timeframe] = tuple(htf_candles)
            continue
        _product, candles, expected_last = await _closed_window_for(
            market_data,
            product_id=covered_product,
            timeframe=timeframe,
            warmup_bars=extra_indicator_timeframe_warmup(indicators),
        )
        interval = parse_candle_interval(timeframe)
        if not htf_coverage_ready(
            candles,
            expected_last_start=expected_last,
            bar_duration=interval.duration,
        ):
            return None
        windows[timeframe] = candles
    return windows


async def _closed_window(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
    """Fetch warmup plus the latest fully closed bar on the strategy interval."""
    return await _closed_window_for(
        market_data,
        product_id=strategy.instrument.product_id,
        timeframe=strategy.timeframe,
        warmup_bars=strategy.data_requirements.warmup_bars,
    )


async def _closed_window_for(
    market_data: MarketDataService,
    *,
    product_id: str,
    timeframe: str,
    warmup_bars: int,
) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
    """Fetch warmup plus the latest fully closed bar on one interval."""
    now = datetime.now(UTC)
    interval = parse_candle_interval(timeframe)
    last_closed_end = interval.align_closed_end(now)
    last_closed_start = last_closed_end - interval.duration
    starts_at = last_closed_start - interval.duration * warmup_bars
    preview = await market_data.get_preview(product_id, interval)
    report = await market_data.get_range(product_id, interval, starts_at, last_closed_end, now)
    candles = tuple(
        candle
        for candle in report.quality.candles
        if starts_at <= candle.starts_at <= last_closed_start
    )
    return preview.product, candles, last_closed_start


async def _currency_available(reader: QuoteBalanceReader, currency: str) -> Decimal | None:
    """Return available units of one venue currency, if reported."""
    balances = await reader.list_balances()
    for balance in balances:
        if balance.currency == currency:
            return balance.available
    return None


async def _portfolio_marks(
    market_data: MarketDataService,
    *,
    portfolio: Sequence[DeploymentSnapshot],
    fallback_timeframe: str,
    current_product_id: str,
    current_close: Decimal,
) -> dict[str, Decimal]:
    """Last-close marks for occupied products so mode-wide daily-loss can fail closed."""
    marks: dict[str, Decimal] = {current_product_id: current_close}
    for snapshot in portfolio:
        timeframe = snapshot.deployment.timeframe or fallback_timeframe
        product_ids = {snapshot.deployment.product_id}
        for runtime in snapshot.instrument_runtimes:
            product_ids.add(runtime.product_id)
        for position in snapshot.positions:
            if position.product_id:
                product_ids.add(position.product_id)
        for product_id in product_ids:
            if product_id in marks:
                continue
            close = await _last_close(market_data, product_id=product_id, timeframe=timeframe)
            if close is not None:
                marks[product_id] = close
    return marks


async def _last_close(
    market_data: MarketDataService, *, product_id: str, timeframe: str
) -> Decimal | None:
    """Return the latest complete close, or None when that window is empty."""
    preview = await market_data.get_preview(product_id, parse_candle_interval(timeframe))
    candles = preview.quality.candles
    if not candles:
        return None
    return candles[-1].close


async def _occupied_snapshots(
    store: ExecutionStore,
    deployments: Sequence[Deployment],
) -> tuple[DeploymentSnapshot, ...]:
    """Load current snapshots for running and paused deployments used by the entry gate."""
    occupied = [
        await store.get_deployment(item.id)
        for item in deployments
        if item.status in {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}
    ]
    return tuple(occupied)


async def _pause_five_minute_live_if_feed_down(
    snapshot: DeploymentSnapshot,
    *,
    timeframe: str,
    store: ExecutionStore,
    user_feed_store: UserOrderFeedStateStore | None,
) -> bool:
    """Pause sub-hour live when the user-order feed is down. True means the cycle must stop."""
    deployment = snapshot.deployment
    if deployment.mode is not DeploymentMode.LIVE:
        return False
    interval = parse_candle_interval(timeframe)
    if not interval.requires_live_user_feed:
        return False
    if await _user_feed_connected(user_feed_store):
        return False
    paused = with_runtime(
        deployment,
        updated_at=utc_now(),
        status=DeploymentStatus.PAUSED,
        mismatch_detail="User-order feed is not connected.",
    )
    await store.save_deployment(paused)
    return True


async def _user_feed_connected(store: UserOrderFeedStateStore | None) -> bool:
    """True only when the durable user-order feed snapshot is connected and fresh."""
    if store is None:
        return False
    try:
        snapshot = await store.get()
    except UserOrderFeedUnavailableError:
        return False
    if snapshot is None or snapshot.state is not UserOrderFeedState.CONNECTED:
        return False
    heartbeat_at = snapshot.last_heartbeat_at
    if heartbeat_at is None:
        return False
    age = (datetime.now(UTC) - heartbeat_at).total_seconds()
    return age < DEFAULT_HEARTBEAT_TIMEOUT_SECONDS
