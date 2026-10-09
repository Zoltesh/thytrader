"""Lockstep multi-instrument strategy evaluation for one execution-worker cycle.

Loads every lockstep product's closed window and filter clocks, evaluates each shared
due bar across the instruments, and journals it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from thytrader.execution.candle_wait import newest_bar_settling
from thytrader.execution.closed_windows import _closed_window_for
from thytrader.execution.decision_journal import record_gate_skip
from thytrader.execution.decisions import DecisionSkipReason
from thytrader.execution.loop import process_closed_bar
from thytrader.execution_worker.bar_journal import _journaled_bar
from thytrader.execution_worker.between_bars import (
    _maintain_between_bars,
    _maintain_multi_between_bars,
)
from thytrader.execution_worker.live_sizing import _currency_available, _prepare_live
from thytrader.execution_worker.strategy_step_common import (
    _latest_due_bar_may_enter,
    _portfolio_marks,
)
from thytrader.execution_worker.supervision import _pause_coverage_gap
from thytrader.execution_worker.windows import (
    _bar_reference_gate,
    _closed_htf_window,
    _closed_indicator_timeframe_windows,
    _closed_reference_windows,
    new_closed_bars,
)
from thytrader.market_data.models import parse_candle_interval
from thytrader.memory.trade_reason_scope import strategy_trade_reason_scope, trade_reason_scope
from thytrader.strategies.models import lockstep_product_ids
from thytrader.trading.geometry import base_currency
from thytrader.trading.ids import utc_now
from thytrader.trading.models import DeploymentMode, DeploymentStatus, with_runtime
from thytrader.trading.overlay import InstrumentScopedStore

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.execution.broker import Broker
    from thytrader.execution_worker.ports import QuoteBalanceReader
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.memory.store import ExperientialMemoryStore
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore


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
