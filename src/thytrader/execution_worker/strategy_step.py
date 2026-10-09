"""Per-deployment strategy evaluation for one execution-worker cycle.

Loads a running book's published strategy, gates it on fresh decision candles, and
advances a single-instrument book over its due closed bars. Lockstep books continue in
``lockstep_step``, between-bar maintenance in ``between_bars``, journaling in
``bar_journal`` and stopped books in ``stopped_step``.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from thytrader.execution.candle_wait import newest_bar_settling
from thytrader.execution.closed_windows import _closed_window
from thytrader.execution.decision_journal import record_gate_skip
from thytrader.execution.decisions import DecisionSkipReason
from thytrader.execution.loop import process_closed_bar
from thytrader.execution_worker.bar_journal import _journaled_bar
from thytrader.execution_worker.between_bars import _maintain_between_bars
from thytrader.execution_worker.live_sizing import _prepare_live, _short_sellable_base
from thytrader.execution_worker.lockstep_step import _advance_multi_instrument
from thytrader.execution_worker.strategy_step_common import (
    _latest_due_bar_may_enter,
    _portfolio_marks,
)
from thytrader.execution_worker.supervision import (
    USER_FEED_PAUSE_DETAIL,
    _pause_five_minute_live_if_feed_down,
    _pause_running_for_data_gap,
    _supervise_warming_window,
    _supervise_without_decision_candles,
)
from thytrader.execution_worker.windows import (
    _bar_reference_gate,
    _closed_htf_window,
    _closed_indicator_timeframe_windows,
    _closed_reference_windows,
    new_closed_bars,
)
from thytrader.market_data.models import parse_candle_interval
from thytrader.market_data.window_state import WindowCacheWarmingError
from thytrader.memory.trade_reason_scope import strategy_trade_reason_scope, trade_reason_scope
from thytrader.strategies.models import lockstep_product_ids
from thytrader.trading.geometry import base_currency
from thytrader.trading.ids import utc_now
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentStatus,
    with_runtime,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.execution.broker import Broker
    from thytrader.execution.user_feed_state import UserOrderFeedStateStore
    from thytrader.execution_worker.ports import QuoteBalanceReader
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.memory.store import ExperientialMemoryStore
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.strategies.snapshots import StrategySnapshotStore
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore


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
    """Advance decision bars, treating bounded cold-cache prefetch as transient."""
    try:
        await _advance_strategy_ready(
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
    except WindowCacheWarmingError:
        await _supervise_warming_window(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
        )


async def _advance_strategy_ready(
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
    """Advance only fully loaded windows; warming propagates to no-entry supervision."""
    deployment = snapshot.deployment
    product, candles, expected_last = await _closed_window(
        market_data, strategy, deploy_anchor=deployment.created_at
    )
    feed_paused = await _pause_five_minute_live_if_feed_down(
        snapshot, timeframe=strategy.timeframe, store=store, user_feed_store=user_feed_store
    )
    if not candles:
        await _supervise_without_decision_candles(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
            product=product,
            expected_last=expected_last,
            feed_paused=feed_paused,
        )
        return
    if feed_paused:
        await record_gate_skip(
            snapshot=snapshot,
            strategy=strategy,
            product_ids=lockstep_product_ids(strategy),
            bar_starts_at=expected_last,
            reason=DecisionSkipReason.USER_FEED_GATE,
            detail=USER_FEED_PAUSE_DETAIL,
        )
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
    interval = parse_candle_interval(strategy.timeframe)
    due = new_closed_bars(
        candles,
        last_evaluated_bar=deployment.last_evaluated_bar,
        expected_last_start=expected_last,
        bar_duration=interval.duration,
        allow_settling=True,
    )
    if due is None:
        detail = "Market-data window is gapped or missing the latest closed bar."
        await _pause_running_for_data_gap(snapshot, store=store, detail=detail)
        await record_gate_skip(
            snapshot=snapshot,
            strategy=strategy,
            product_ids=lockstep_product_ids(strategy),
            bar_starts_at=expected_last,
            reason=DecisionSkipReason.DATA_GAP,
            detail=detail,
        )
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
    if not due:
        if newest_bar_settling(
            candles, expected_last_start=expected_last, bar_duration=interval.duration
        ):
            await record_gate_skip(
                snapshot=snapshot,
                strategy=strategy,
                product_ids=lockstep_product_ids(strategy),
                bar_starts_at=expected_last,
                reason=DecisionSkipReason.BAR_SETTLING,
                detail="Waiting for the newest closed candle; no new entries (two-minute limit).",
            )
            if len(lockstep_product_ids(strategy)) > 1:
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
                    due=(),
                    memory_store=memory_store,
                )
                return
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
    htf_candles = await _closed_htf_window(
        market_data, strategy, deploy_anchor=deployment.created_at
    )
    if htf_candles is None:
        detail = "HTF market-data window is gapped or missing the latest completed HTF bar."
        await _pause_running_for_data_gap(snapshot, store=store, detail=detail)
        await record_gate_skip(
            snapshot=snapshot,
            strategy=strategy,
            product_ids=(product.product_id,),
            bar_starts_at=due[-1].starts_at,
            reason=DecisionSkipReason.DATA_GAP,
            detail=detail,
        )
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
    deployment = snapshot.deployment
    extra_candles = await _indicator_timeframe_windows_or_pause(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=market_data,
        htf_candles=htf_candles,
        deploy_anchor=deployment.created_at,
    )
    if extra_candles is None:
        await record_gate_skip(
            snapshot=snapshot,
            strategy=strategy,
            product_ids=(product.product_id,),
            bar_starts_at=due[-1].starts_at,
            reason=DecisionSkipReason.DATA_GAP,
            detail="Indicator-timeframe market-data window is gapped.",
        )
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
    reference_candles = await _closed_reference_windows(
        market_data, strategy, deploy_anchor=deployment.created_at
    )
    broker: Broker = paper_broker
    fee_profile: FeeProfile | None = None
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
        snapshot, fee_profile = prepared
        broker = live_broker
    last_index = len(due) - 1
    for index, candle in enumerate(due):
        current = await store.get_deployment(deployment.id)
        if current.deployment.status is DeploymentStatus.STOPPED:
            return
        window = tuple(item for item in candles if item.starts_at <= candle.starts_at)
        live_base_available = None
        if current.deployment.mode is DeploymentMode.LIVE and quote_reader is not None:
            live_base_available = await _short_sellable_base(
                quote_reader,
                base_currency(product.product_id),
                portfolio=portfolio,
                current=current,
            )
        marks = await _portfolio_marks(
            market_data,
            portfolio=portfolio,
            fallback_timeframe=strategy.timeframe,
            current_product_id=product.product_id,
            current_close=candle.close,
        )
        allow_new_entries = _latest_due_bar_may_enter(
            candle,
            timeframe=strategy.timeframe,
            is_latest=index == last_index,
        )
        gate = _bar_reference_gate(
            strategy, reference_candles, candle, allow_new_entries=allow_new_entries
        )
        if gate is not None:
            allow_new_entries = False
        with trade_reason_scope(
            strategy_trade_reason_scope(
                memory_store,
                deployment=current.deployment,
                strategy=strategy,
                policy=risk_policy,
            )
        ):
            await _journaled_bar(
                current,
                strategy=strategy,
                product_id=product.product_id,
                candle=candle,
                allow_new_entries=allow_new_entries,
                reference_gate=gate,
                advance=partial(
                    process_closed_bar,
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
                    reference_candles=reference_candles,
                    live_base_available=live_base_available,
                    marks=marks,
                    fee_profile=fee_profile,
                    allow_new_entries=allow_new_entries,
                ),
            )


async def _strategy_definition(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    publication_store: StrategySnapshotStore,
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


async def _indicator_timeframe_windows_or_pause(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    htf_candles: Sequence[Candle],
    deploy_anchor: datetime,
    as_of_closed_start: datetime | None = None,
) -> dict[str, tuple[Candle, ...]] | None:
    """Return extra-TF windows, or pause when that complete-only coverage is missing."""
    extra_candles = await _closed_indicator_timeframe_windows(
        market_data,
        strategy,
        htf_candles,
        deploy_anchor=deploy_anchor,
        as_of_closed_start=as_of_closed_start,
    )
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
