"""Live order inputs shared by the discretionary order route and live agent desks.

The broker for a request's mode and the venue balances a live entry is sized and gated by:
remaining quote, and base no live book claims (ADR 0124).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.trading.geometry import base_currency
from thytrader.trading.inventory_claims import unmanaged_available_base
from thytrader.trading.models import DeploymentMode, ExecutionConflictError, ExecutionStoreError

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.exchanges.models import ExchangeBalance
    from thytrader.exchanges.protocols import ExchangeAccount
    from thytrader.execution.broker import Broker
    from thytrader.execution.discretionary_request import DiscretionaryOrderRequest
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore


def broker_for_request(
    request: DiscretionaryOrderRequest,
    *,
    paper_broker: Broker,
    live_broker: Broker | None,
) -> Broker:
    """Select the paper or live broker; live still requires credentials."""
    if request.mode is DeploymentMode.PAPER:
        return paper_broker
    if live_broker is None:
        raise ExecutionConflictError("Live trading requires configured Coinbase credentials.")
    return live_broker


async def unmanaged_base_available(
    quote_reader: ExchangeAccount | None,
    *,
    store: ExecutionStore,
    mode: DeploymentMode,
    product_id: str,
) -> Decimal | None:
    """Base a live short may sell: available base no live book claims (ADR 0124).

    Raw ``available`` includes base a managed long owns but has not yet protected.
    Any unreadable live book leaves the quantity unknown, which refuses the short.
    """
    if mode is not DeploymentMode.LIVE or quote_reader is None:
        return None
    balances: tuple[ExchangeBalance, ...] = await quote_reader.list_balances()
    books: list[DeploymentSnapshot] = []
    for deployment in await store.list_deployments():
        if deployment.mode is not DeploymentMode.LIVE:
            continue
        try:
            books.append(await store.get_accounting_snapshot(deployment.id))
        except ExecutionStoreError:
            return None
    return unmanaged_available_base(balances, books, base_currency(product_id))


async def currency_available(
    quote_reader: ExchangeAccount | None, *, mode: DeploymentMode, currency: str
) -> Decimal | None:
    """Return remaining units of one venue currency when a live reader is attached."""
    if mode is not DeploymentMode.LIVE or quote_reader is None:
        return None
    balances: tuple[ExchangeBalance, ...] = await quote_reader.list_balances()
    for balance in balances:
        if balance.currency == currency:
            return balance.available
    return None
