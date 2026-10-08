"""Between-bar maintenance of open strategy books.

Keeps protection and open inventory supervised when no new closed decision bar is due,
for single-instrument and lockstep multi-instrument books.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from thytrader.execution.loop import maintain_open_inventory
from thytrader.execution.stopped import stopped_product_ids
from thytrader.execution_worker.bar_journal import _journaled_bar
from thytrader.execution_worker.live_sizing import _prepare_live
from thytrader.execution_worker.supervision import _maintain_verified_books
from thytrader.trading.models import DeploymentMode
from thytrader.trading.overlay import InstrumentScopedStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.execution.broker import Broker
    from thytrader.execution_worker.ports import QuoteBalanceReader
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore


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
