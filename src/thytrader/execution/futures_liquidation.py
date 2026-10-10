"""The paper futures liquidation monitor (ADR 0129 §4).

Paper sees closed bars only, so the check runs on each closed bar of a paper futures book,
before the protective stop: when book equity at the bar's adverse extreme (low for longs,
high for shorts) is below maintenance at the observed overnight rates, the book sends a
``LIQUIDATION``-purpose marketable exit at that extreme. The same conservative rule as the
backtest kernel (ADR 0128 §3); the venue would liquidate at mark. Unknown margin terms skip
the check (and deny new entries), they never imply a liquidation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.exits import _marketable_exit
from thytrader.trading.futures_book import current_futures_book, liquidation_due
from thytrader.trading.models import DeploymentMode, IntentPurpose, PositionSide

if TYPE_CHECKING:
    from thytrader.execution.broker import Broker
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.models import DeploymentSnapshot, Position
    from thytrader.trading.store import ExecutionStore


async def paper_liquidation_if_due(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    position: Position,
) -> DeploymentSnapshot | None:
    """Liquidate a paper futures book whose adverse-extreme equity is below maintenance."""
    if snapshot.deployment.mode is not DeploymentMode.PAPER:
        return None
    state = current_futures_book(snapshot.deployment.id)
    if state is None or state.margin is None:
        return None
    short = position.side is PositionSide.SHORT
    adverse = candle.high if short else candle.low
    if not liquidation_due(
        state.margin,
        cash=snapshot.deployment.cash,
        quantity=position.quantity,
        side="short" if short else "long",
        adverse_price=adverse,
    ):
        return None
    return await _marketable_exit(
        snapshot,
        strategy=strategy,
        candle=candle,
        product=product,
        broker=broker,
        store=store,
        purpose=IntentPurpose.LIQUIDATION,
        price=adverse,
    )
