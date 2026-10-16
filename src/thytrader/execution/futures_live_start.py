"""Starting a live futures book: its USD capital and fresh venue evidence (ADR 0134 P2-7).

A live futures book is standalone (never a portfolio sleeve or an adoption) and states its own
``futures_capital_usd``: that is its allocated and performance capital (ADR 0107), drawn from
the policy's ``futures.live_capital_usd`` envelope. The start reads the CFM account once (GET
only) for the risk gate's I3/I8 checks; a failed read is unknown evidence, which the gate denies.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta
from decimal import Decimal, DecimalException
from typing import TYPE_CHECKING

from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.risk.futures_live import LiveFuturesStart
from thytrader.trading.ids import utc_now
from thytrader.trading.lifecycle import occupies_running_slot
from thytrader.trading.models import DeploymentMode, ExecutionConflictError, OrderStatus

if TYPE_CHECKING:
    from thytrader.execution.futures_live import FuturesLiveSource
    from thytrader.execution.futures_start import FuturesStart, FuturesStartTerms
    from thytrader.market_data.service import MarketDataService
    from thytrader.risk.futures_live import FuturesVenueEvidence
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.models import Deployment
    from thytrader.trading.store import ExecutionStore

_ACTIVE = frozenset({OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN})
_PRICE_MAX_AGE = timedelta(hours=2)


def require_live_futures_capital(
    definition: StrategyDefinition,
    *,
    mode: DeploymentMode,
    portfolio_sleeve: bool,
    capital: Decimal | None,
) -> Decimal | None:
    """The book's USD capital for a standalone live futures start; ``None`` otherwise."""
    live_futures = mode is DeploymentMode.LIVE and definition.instrument.is_future
    if not live_futures:
        if capital is not None:
            raise ExecutionConflictError(
                "futures_capital_usd applies to live futures strategies only; a paper futures "
                "book takes its USD from paper_starting_cash."
            )
        return None
    if portfolio_sleeve:
        raise ExecutionConflictError(
            "FUTURES_LIVE_UNSUPPORTED: a live futures book starts standalone, not as a "
            "portfolio sleeve."
        )
    if capital is None or not capital.is_finite() or capital <= 0:
        raise ExecutionConflictError(
            "FUTURES_CAPITAL_REQUIRED: a live futures start needs futures_capital_usd, a "
            "positive USD amount inside the risk policy's futures.live_capital_usd."
        )
    return capital


async def live_futures_start(
    *,
    capital: Decimal,
    terms: FuturesStartTerms,
    start: FuturesStart | None,
    store: ExecutionStore,
    deployments: tuple[Deployment, ...],
    market_data: MarketDataService | None,
) -> LiveFuturesStart:
    """What the risk gate admits a live futures start on; missing evidence stays ``None``.

    The proposed initial margin is the smallest entry's: one contract at the latest hourly
    close times the catalog's higher overnight rate. Unknown inputs leave it ``None`` and the
    gate denies (I8); buying power must cover at least one contract to start.
    """
    product_id = terms.contract.product_id
    source = None if start is None else start.live_source
    margin = await _one_contract_margin(terms, market_data)
    venue = await _venue_evidence(
        source, store=store, deployments=deployments, product_id=product_id
    )
    # Admission time is taken after every awaited read, so the gate's 180 s freshness check
    # covers all of them.
    return LiveFuturesStart(
        allocated_capital_usd=capital,
        contract=terms.contract,
        venue=venue,
        as_of=utc_now(),
        proposed_initial_margin_usd=margin,
    )


async def _one_contract_margin(
    terms: FuturesStartTerms, market_data: MarketDataService | None
) -> Decimal | None:
    """Initial margin of one contract at the latest closed hourly price, or None if unknown."""
    if market_data is None or terms.initial_margin_rate is None:
        return None
    price = await _latest_close(market_data, terms.contract.product_id)
    if price is None:
        return None
    margin = Decimal(terms.contract.contract_size) * price * terms.initial_margin_rate
    return margin if margin.is_finite() and margin > 0 else None


async def _latest_close(market_data: MarketDataService, product_id: str) -> Decimal | None:
    """The latest closed hourly close, or None when the read fails, is stale or has no candle.

    The close must belong to a fresh preview and to one of the last two closed hours.
    """
    with contextlib.suppress(RuntimeError, ValueError, TypeError, OSError, DecimalException):
        quality = (await market_data.get_hourly_preview(product_id)).quality
        latest = quality.latest_completed_at
        if quality.is_stale or not quality.candles or latest is None:
            return None
        if utc_now() - latest > _PRICE_MAX_AGE:
            return None
        return quality.candles[-1].close
    return None


async def _venue_evidence(
    source: FuturesLiveSource | None,
    *,
    store: ExecutionStore,
    deployments: tuple[Deployment, ...],
    product_id: str,
) -> FuturesVenueEvidence | None:
    """One GET-only CFM read; orders of running or paused live futures books are not external.

    A stopped book's leftover orders count as external, so a new start on that perp is refused
    until they are gone (``close_position`` acts on the whole account position).
    """
    if source is None:
        return None
    managed: set[tuple[str, str]] = set()
    for deployment in deployments:
        if (
            deployment.mode is not DeploymentMode.LIVE
            or not is_futures_product_id(deployment.product_id)
            or not occupies_running_slot(deployment)
        ):
            continue
        snapshot = await store.get_deployment(deployment.id)
        managed.update(
            (order.product_id, order.client_order_id)
            for order in snapshot.orders
            if order.status in _ACTIVE
        )
    with contextlib.suppress(RuntimeError, ValueError, TypeError, OSError, DecimalException):
        account = await source.read_account(frozenset(managed), frozenset({product_id}))
        return account.venue
    return None
