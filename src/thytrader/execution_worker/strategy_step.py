"""Per-deployment strategy evaluation for one execution-worker cycle.

Advances single- and multi-instrument (lockstep) strategy books over due closed
bars, journals each bar, maintains books between bars, and supervises stopped books.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from thytrader.execution.candle_wait import newest_bar_settling
from thytrader.execution.decision_journal import observe_bar, record_bar_decision, record_gate_skip
from thytrader.execution.decision_scope import note_reference_gate
from thytrader.execution.decisions import DecisionSkipReason
from thytrader.execution.freshness import signal_still_valid
from thytrader.execution.loop import maintain_open_inventory, process_closed_bar
from thytrader.execution.stopped import stopped_product_ids, supervise_stopped_deployment
from thytrader.execution.trade_reason_scope import strategy_trade_reason_scope, trade_reason_scope
from thytrader.execution_worker.live_sizing import _currency_available, _prepare_live
from thytrader.execution_worker.supervision import (
    USER_FEED_PAUSE_DETAIL,
    _maintain_verified_books,
    _pause_coverage_gap,
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
    _closed_window,
    _closed_window_for,
    _signal_exit_windows,
    new_closed_bars,
)
from thytrader.market_data.models import parse_candle_interval
from thytrader.market_data.window_state import WindowCacheWarmingError
from thytrader.strategies.models import lockstep_product_ids, signal_exit_condition
from thytrader.trading.geometry import base_currency
from thytrader.trading.ids import utc_now
from thytrader.trading.models import (
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    with_runtime,
)
from thytrader.trading.overlay import InstrumentScopedStore

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.execution.broker import Broker
    from thytrader.execution.references import ReferenceGate
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


def _latest_due_bar_may_enter(candle: Candle, *, timeframe: str, is_latest: bool) -> bool:
    """True when this recovered close is the newest due bar and still within max age."""
    return is_latest and signal_still_valid(
        candle=candle,
        timeframe=timeframe,
        now=utc_now(),
        current_quote=candle.close,
    )


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
    loaded = await _load_lockstep_product_windows(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=market_data,
        covered=covered,
        primary_product=primary_product,
        primary_candles=primary_candles,
    )
    decision_start = (
        due[-1].starts_at
        if due
        else parse_candle_interval(strategy.timeframe).align_closed_end(utc_now())
        - parse_candle_interval(strategy.timeframe).duration
    )
    if loaded is None:
        await record_gate_skip(
            snapshot=snapshot,
            strategy=strategy,
            product_ids=covered,
            bar_starts_at=decision_start,
            reason=DecisionSkipReason.DATA_GAP,
            detail="A covered product's market-data window is gapped.",
        )
        await _maintain_between_bars(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
            quote_reader=quote_reader,
            product=primary_product,
            candles=primary_candles,
        )
        return
    windows = loaded.windows
    if loaded.settling:
        await record_gate_skip(
            snapshot=snapshot,
            strategy=strategy,
            product_ids=covered,
            bar_starts_at=decision_start,
            reason=DecisionSkipReason.BAR_SETTLING,
            detail="A covered product's newest closed candle is settling; no new entries.",
        )
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
    deployment = snapshot.deployment
    overlays = await _load_lockstep_filter_windows(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=market_data,
        covered=covered,
        deploy_anchor=deployment.created_at,
    )
    if overlays is None:
        await record_gate_skip(
            snapshot=snapshot,
            strategy=strategy,
            product_ids=covered,
            bar_starts_at=due[-1].starts_at,
            reason=DecisionSkipReason.DATA_GAP,
            detail="A covered product's HTF or indicator-timeframe window is gapped.",
        )
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
    htf_by_product, extra_by_product = overlays
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
            product_id=primary_product.product_id,
            cooldown_bars=strategy.entry.cooldown_bars,
        )
        if prepared is None or live_broker is None:
            return
        snapshot, fee_profile = prepared
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
    for index, candle in enumerate(due):
        stopped = await _evaluate_lockstep_bar(
            candle,
            covered=covered,
            windows=windows,
            htf_by_product=htf_by_product,
            extra_by_product=extra_by_product,
            reference_candles=reference_candles,
            deployment_id=deployment.id,
            strategy=strategy,
            store=store,
            market_data=market_data,
            broker=broker,
            quote_reader=quote_reader,
            risk_policy=risk_policy,
            portfolio=portfolio,
            memory_store=memory_store,
            fee_profile=fee_profile,
            allow_new_entries=_latest_due_bar_may_enter(
                candle,
                timeframe=strategy.timeframe,
                is_latest=index == len(due) - 1,
            ),
        )
        if stopped:
            return


@dataclass(frozen=True, slots=True)
class LockstepProductWindows:
    """Covered decision-clock windows and whether any newest candle is settling."""

    windows: dict[str, tuple[MarketProduct, tuple[Candle, ...]]]
    settling: bool


async def _load_lockstep_product_windows(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    covered: tuple[str, ...],
    primary_product: MarketProduct,
    primary_candles: Sequence[Candle],
) -> LockstepProductWindows | None:
    """Load closed LTF windows for every covered product, or pause on a gap."""
    windows: dict[str, tuple[MarketProduct, tuple[Candle, ...]]] = {
        primary_product.product_id: (primary_product, tuple(primary_candles))
    }
    interval = parse_candle_interval(strategy.timeframe)
    expected = interval.align_closed_end(utc_now()) - interval.duration
    if (
        new_closed_bars(
            primary_candles,
            last_evaluated_bar=snapshot.deployment.last_evaluated_bar,
            expected_last_start=expected,
            bar_duration=interval.duration,
            allow_settling=True,
        )
        is None
    ):
        await _pause_coverage_gap(snapshot, store=store, product_id=primary_product.product_id)
        return None
    settling = newest_bar_settling(
        primary_candles, expected_last_start=expected, bar_duration=interval.duration
    )
    for product_id in covered:
        if product_id in windows:
            continue
        extra_product, extra_candles, extra_expected = await _closed_window_for(
            market_data,
            product_id=product_id,
            timeframe=strategy.timeframe,
            warmup_bars=strategy.data_requirements.warmup_bars,
            deploy_anchor=snapshot.deployment.created_at,
        )
        extra_due = new_closed_bars(
            extra_candles,
            last_evaluated_bar=snapshot.deployment.last_evaluated_bar,
            expected_last_start=extra_expected,
            bar_duration=interval.duration,
            allow_settling=True,
        )
        if extra_due is None:
            await _pause_coverage_gap(snapshot, store=store, product_id=product_id)
            return None
        windows[product_id] = (extra_product, extra_candles)
        settling = settling or newest_bar_settling(
            extra_candles, expected_last_start=extra_expected, bar_duration=interval.duration
        )
    return LockstepProductWindows(windows=windows, settling=settling)


async def _load_lockstep_filter_windows(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    covered: tuple[str, ...],
    deploy_anchor: datetime,
) -> tuple[dict[str, tuple[Candle, ...]], dict[str, dict[str, tuple[Candle, ...]]]] | None:
    """Load last-completed HTF and extra-TF windows, or pause on a gap."""
    htf_by_product: dict[str, tuple[Candle, ...]] = {}
    extra_by_product: dict[str, dict[str, tuple[Candle, ...]]] = {}
    for product_id in covered:
        htf_candles = await _closed_htf_window(
            market_data, strategy, product_id=product_id, deploy_anchor=deploy_anchor
        )
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
            market_data,
            strategy,
            htf_candles,
            product_id=product_id,
            deploy_anchor=deploy_anchor,
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
    reference_candles: dict[str, tuple[Candle, ...]],
    deployment_id: UUID,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    broker: Broker,
    quote_reader: QuoteBalanceReader | None,
    risk_policy: RiskPolicyDefinition,
    portfolio: tuple[DeploymentSnapshot, ...],
    memory_store: ExperientialMemoryStore | None,
    fee_profile: FeeProfile | None = None,
    allow_new_entries: bool = True,
) -> bool:
    """Evaluate every covered product on one shared closed bar. True if the loop should stop.

    One reference-instrument gate applies to every covered product on the shared bar.
    """
    current = await store.get_deployment(deployment_id)
    if current.deployment.status is DeploymentStatus.STOPPED:
        return True
    gate = _bar_reference_gate(
        strategy, reference_candles, candle, allow_new_entries=allow_new_entries
    )
    if gate is not None:
        allow_new_entries = False
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
            await _journaled_bar(
                focused,
                strategy=strategy,
                product_id=product_id,
                candle=bar,
                allow_new_entries=allow_new_entries,
                reference_gate=gate,
                advance=partial(
                    process_closed_bar,
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
                    reference_candles=reference_candles,
                    live_base_available=live_base_available,
                    marks=marks,
                    fee_profile=fee_profile,
                    allow_new_entries=allow_new_entries,
                ),
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


async def _journaled_bar(
    before: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product_id: str,
    candle: Candle,
    allow_new_entries: bool,
    advance: Callable[[], Awaitable[DeploymentSnapshot]],
    require_activity: bool = False,
    reference_gate: ReferenceGate | None = None,
) -> DeploymentSnapshot:
    """Run one closed-bar call and journal what it decided (ADR 0087).

    The call runs exactly as without a journal. A bar that was already evaluated
    (between-bar protection) is not journaled again. ``require_activity`` (flatten
    passes, priced on the latest closed bar) journals only when the call created
    intents or fills, even on an already evaluated bar. A raised call is journaled
    as an error and re-raised unchanged. ``reference_gate`` records why a stale or
    missing reference instrument blocked entries on this bar (ADR 0096).
    """
    if not require_activity and before.deployment.last_evaluated_bar == candle.starts_at:
        return await advance()
    with observe_bar() as observations:
        if reference_gate is not None:
            note_reference_gate(reference_gate)
        try:
            after = await advance()
        except Exception as error:
            await record_bar_decision(
                strategy=strategy,
                product_id=product_id,
                candle=candle,
                before=before,
                after=None,
                observations=observations,
                allow_new_entries=allow_new_entries,
                error=(
                    f"closed-bar processing raised {type(error).__name__}; "
                    "the cycle retries next interval"
                ),
            )
            raise
    if require_activity and not _bar_had_activity(before, after):
        return after
    await record_bar_decision(
        strategy=strategy,
        product_id=product_id,
        candle=candle,
        before=before,
        after=after,
        observations=observations,
        allow_new_entries=allow_new_entries,
    )
    return after


def _bar_had_activity(before: DeploymentSnapshot, after: DeploymentSnapshot) -> bool:
    """Whether a closed-bar call created intents or recorded fills."""
    return len(after.intents) != len(before.intents) or len(after.fills) != len(before.fills)


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
    """Reconcile and supervise only product-scoped inventory between decision bars."""
    snapshot = await store.get_deployment(snapshot.deployment.id)
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
        snapshot, _fee_profile = prepared
        broker = live_broker
    if len(stopped_product_ids(snapshot, strategy)) > 1:
        await _maintain_verified_books(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=market_data,
            paper_broker=paper_broker,
            live_broker=live_broker,
        )
        return
    if not candles:
        return
    scoped = InstrumentScopedStore(store, product.product_id)
    focused = await scoped.get_deployment(snapshot.deployment.id)
    await _journaled_bar(
        focused,
        strategy=strategy,
        product_id=product.product_id,
        candle=candles[-1],
        allow_new_entries=False,
        advance=partial(
            maintain_open_inventory,
            focused,
            strategy=strategy,
            product=product,
            candles=candles,
            broker=broker,
            store=scoped,
        ),
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
    snapshot = await store.get_deployment(snapshot.deployment.id)
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
        snapshot, _fee_profile = prepared
        broker = live_broker
    for product_id in covered:
        product, candles = windows[product_id]
        if not candles:
            continue
        scoped = InstrumentScopedStore(store, product_id)
        focused = await scoped.get_deployment(snapshot.deployment.id)
        await _journaled_bar(
            focused,
            strategy=strategy,
            product_id=product_id,
            candle=candles[-1],
            allow_new_entries=False,
            advance=partial(
                maintain_open_inventory,
                focused,
                strategy=strategy,
                product=product,
                candles=candles,
                broker=broker,
                store=scoped,
            ),
        )


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
