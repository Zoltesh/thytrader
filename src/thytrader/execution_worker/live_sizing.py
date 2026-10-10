"""Live-book preparation before sizing: fills, venue quote cash, and fee tier."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.capital import apply_venue_quote
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution_worker.ports import QuoteBalanceReader, _logger
from thytrader.market_data.cycle_reads import shared_read
from thytrader.trading.ids import utc_now
from thytrader.trading.inventory_claims import unmanaged_available_base
from thytrader.trading.models import DeploymentStatus, with_runtime

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.execution.broker import Broker
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore


async def _prepare_live(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    live_broker: Broker | None,
    quote_reader: QuoteBalanceReader | None,
    quote_currency: str,
    product_id: str,
    cooldown_bars: int,
) -> tuple[DeploymentSnapshot, FeeProfile | None] | None:
    """Pause without a live broker, else reconcile fills and refresh quote cash and fees."""
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
        return snapshot, None
    if quote_reader is not None:
        available = await _currency_available(quote_reader, quote_currency)
        current = await store.get_deployment(deployment.id)
        stamped = apply_venue_quote(current.deployment, available=available, now=utc_now())
        await store.save_deployment(stamped)
        snapshot = await store.get_deployment(deployment.id)
    fee_profile = await _live_fee_profile(quote_reader)
    return snapshot, fee_profile


async def _live_fee_profile(
    quote_reader: QuoteBalanceReader | None,
) -> FeeProfile | None:
    """Return the venue maker tier when the quote reader exposes fee evidence."""
    if quote_reader is None:
        return None
    get_profile = getattr(quote_reader, "get_fee_profile", None)
    if get_profile is None:
        return None
    try:
        # One fee-tier read per cycle serves every live book (ADR 0131).
        return await shared_read(("fee_profile", str(id(quote_reader))), get_profile)
    except RuntimeError, ValueError, TypeError, OSError:
        _logger.exception("live_fee_profile_fetch_failed")
        return None


async def _currency_available(reader: QuoteBalanceReader, currency: str) -> Decimal | None:
    """Return available units of one venue currency, if reported."""
    balances = await reader.list_balances()
    for balance in balances:
        if balance.currency == currency:
            return balance.available
    return None


async def _short_sellable_base(
    reader: QuoteBalanceReader,
    base: str,
    *,
    portfolio: Sequence[DeploymentSnapshot],
    current: DeploymentSnapshot,
) -> Decimal | None:
    """Base a live short may sell: available base no live book claims (ADR 0124).

    ``portfolio`` is the cycle's full risk snapshots; ``current`` replaces this book's
    older row. Raw ``available`` includes base a managed long owns but has not yet
    protected, so it is never the answer on its own. Unknown claims refuse the short.
    """
    balances = await reader.list_balances()
    books = (
        *(item for item in portfolio if item.deployment.id != current.deployment.id),
        current,
    )
    return unmanaged_available_base(balances, books, base)
