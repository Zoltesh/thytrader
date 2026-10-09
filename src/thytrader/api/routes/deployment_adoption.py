"""``POST /api/v1/deployments`` with ``adopt_holdings``: a live start already holding coins.

The route delegates here when the request names ``adopt_holdings`` (ADR 0124). Paper is
refused with ``ADOPTION_LIVE_ONLY``; live needs the adoption store and the live account.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.api.routes.inventory_adoption import quote_available
from thytrader.execution.adoption import ADOPTION_LIVE_ONLY
from thytrader.execution.adoption_strategy import StrategyAdoptionVenue, start_with_adoption
from thytrader.trading.adoption_write import AdoptionRefusedError
from thytrader.trading.models import DeploymentMode, ExecutionConflictError, ExecutionStoreError

if TYPE_CHECKING:
    from thytrader.exchanges.protocols import ExchangeAccount
    from thytrader.execution.service import ReferenceWatchlist
    from thytrader.market_data.service import MarketDataService
    from thytrader.memory.store import ExperientialMemoryStore
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.strategies.snapshots import StrategySnapshot, StrategySnapshotReader
    from thytrader.trading.models import Deployment
    from thytrader.trading.store import ExecutionStore, InventoryAdoptionStore


async def start_adopting(
    *,
    mode: DeploymentMode,
    adopt_holdings: str,
    snapshot: StrategySnapshot,
    store: ExecutionStore,
    publication_store: StrategySnapshotReader,
    live_allowed: bool,
    risk_store: RiskPolicyStore,
    reference_watches: ReferenceWatchlist,
    adoption_store: InventoryAdoptionStore | None,
    market_data: MarketDataService,
    quote_reader: ExchangeAccount | None,
    memory_store: ExperientialMemoryStore | None,
) -> Deployment:
    """Start the strategy live with its held coins adopted, or refuse with a code.

    Raises:
        ExecutionConflictError: Paper, no live account, or any start or adoption refusal.
        ExecutionStoreError: The adoption store is unavailable.
    """
    if mode is not DeploymentMode.LIVE:
        raise AdoptionRefusedError(
            ADOPTION_LIVE_ONLY, "adopt_holdings is live-only: paper has no venue holdings."
        )
    if adoption_store is None:
        raise ExecutionStoreError("Inventory adoption storage is unavailable.")
    if quote_reader is None:
        raise ExecutionConflictError("Live trading requires configured Coinbase credentials.")
    started = await start_with_adoption(
        store=store,
        publication_store=publication_store,
        strategy_fingerprint=snapshot.strategy_fingerprint,
        quantity=None if adopt_holdings == "all" else Decimal(adopt_holdings),
        live_allowed=live_allowed,
        risk_store=risk_store,
        reference_watches=reference_watches,
        venue=StrategyAdoptionVenue(
            adoption_store=adoption_store,
            market_data=market_data,
            read_balances=quote_reader.list_balances,
            live_quote_cash=await quote_available(
                quote_reader, snapshot.definition.instrument.product_id
            ),
            memory_store=memory_store,
        ),
    )
    return started.deployment
