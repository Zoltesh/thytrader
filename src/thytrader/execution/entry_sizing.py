"""Price, size, and key one closed-bar entry order.

Awaits the broker's maker limit price, sizes a new book or a same-side pyramid add
at the entry fee rate, stamps the pending stop and target levels on the book, and
derives the deterministic signal intent key that deduplicates entry intents.
"""

from __future__ import annotations

from decimal import Decimal
import inspect
from typing import TYPE_CHECKING

from thytrader.execution.broker import BrokerError
from thytrader.execution.capital import live_sizing_cash
from thytrader.strategies.models import atr_trailing_stop
from thytrader.trading.geometry import EntrySkipReason
from thytrader.trading.ids import utc_now
from thytrader.trading.ledger import PAPER_MAKER_FEE_RATE, effective_paper_fee_rates
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    IntentPurpose,
    OrderSide,
    PositionSide,
    RuntimePhase,
    with_runtime,
)
from thytrader.trading.sizing import SizedEntry, size_entry_or_skip, size_pyramid_add_or_skip

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.execution.broker import Broker
    from thytrader.market_data.models import MarketProduct
    from thytrader.strategies.models import StrategyDefinition


def _size_entry_or_add(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    entry_price: Decimal,
    atr: Decimal,
    side: PositionSide,
    is_pyramid_add: bool,
    fee_profile: FeeProfile | None = None,
) -> SizedEntry | EntrySkipReason:
    """Size a new book or a same-side add against remaining quote cash, or name the skip."""
    if (
        strategy.entry.economic_guard is not None
        and snapshot.deployment.mode is DeploymentMode.LIVE
        and fee_profile is None
    ):
        return EntrySkipReason.ECONOMICS_FEE_UNAVAILABLE
    fee_rate = _entry_fee_rate(snapshot.deployment, fee_profile=fee_profile)
    sizing_cash = live_sizing_cash(snapshot.deployment)
    if sizing_cash is None:
        return EntrySkipReason.SIZING_CASH_UNAVAILABLE
    if not is_pyramid_add:
        return size_entry_or_skip(
            strategy=strategy,
            cash=sizing_cash,
            entry_price=entry_price,
            atr=atr,
            product=product,
            fee_rate=fee_rate,
            side=side,
        )
    position = snapshot.position
    if position is None:
        return EntrySkipReason.NO_OPEN_POSITION
    return size_pyramid_add_or_skip(
        strategy=strategy,
        cash=sizing_cash,
        entry_price=entry_price,
        existing_stop=position.stop_price,
        existing_target=position.target_price,
        product=product,
        fee_rate=fee_rate,
        side=side,
    )


def _runtime_for_admitted_entry(
    deployment: Deployment,
    *,
    sized: SizedEntry,
    strategy: StrategyDefinition,
    is_pyramid_add: bool,
) -> tuple[Deployment, bool]:
    """Stamp pending-entry or keep OPEN, and decide whether live brackets attach.

    A Coinbase attached ``trigger_bracket_gtc`` needs a take-profit limit, so an entry
    without a target never attaches; its stop-only protection rests after the fill.
    """
    if is_pyramid_add:
        pending = with_runtime(
            deployment,
            updated_at=utc_now(),
            phase=RuntimePhase.OPEN,
            pending_entry_bars=0,
        )
        return pending, False
    pending = with_runtime(
        _with_pending_levels(
            deployment, stop_price=sized.stop_price, target_price=sized.target_price
        ),
        updated_at=utc_now(),
        phase=RuntimePhase.PENDING_ENTRY,
        pending_entry_bars=0,
    )
    attach = sized.target_price is not None and atr_trailing_stop(strategy.exits) is None
    return pending, attach


def _with_pending_levels(
    deployment: Deployment, *, stop_price: Decimal, target_price: Decimal | None
) -> Deployment:
    """Replace both pending levels; a None target (no take-profit) clears any stale one."""
    cleared = with_runtime(deployment, updated_at=utc_now(), clear_pending_levels=True)
    return with_runtime(
        cleared,
        updated_at=utc_now(),
        pending_stop_price=stop_price,
        pending_target_price=target_price,
    )


async def _await_maker_limit(
    broker: Broker,
    *,
    product_id: str,
    mark: Decimal,
    side: OrderSide,
) -> Decimal:
    """Await maker_limit_price whether the broker implements it as async or sync.

    CoinbaseRestBroker keeps a sync implementation (owned by a sibling slice); paper
    and tests may be sync or async. Network I/O wrapping stays outside this helper.
    """
    result = broker.maker_limit_price(product_id=product_id, mark=mark, side=side)
    if inspect.isawaitable(result):
        awaited = await result
        if isinstance(awaited, Decimal):
            return awaited
        raise BrokerError("Maker entry price is unavailable.")
    if isinstance(result, Decimal):
        return result
    raise BrokerError("Maker entry price is unavailable.")


def _signal_intent_key(
    deployment_id: UUID, purpose: IntentPurpose, candle_starts_at: datetime, product_id: str
) -> str:
    """Stable command/signal identity used to deduplicate order intents."""
    stamp = candle_starts_at.strftime("%Y%m%dT%H%M")
    return f"{deployment_id}:{purpose.value}:{product_id}:{stamp}"[:128]


def _entry_fee_rate(deployment: Deployment, *, fee_profile: FeeProfile | None = None) -> Decimal:
    """Size entries with paper assumptions or the live Coinbase maker tier when available."""
    if deployment.mode is DeploymentMode.PAPER:
        maker_fee_rate, _taker_fee_rate = effective_paper_fee_rates(
            deployment.paper_maker_fee_rate, deployment.paper_taker_fee_rate
        )
        return maker_fee_rate
    if fee_profile is not None:
        return fee_profile.maker_fee_rate
    return PAPER_MAKER_FEE_RATE
