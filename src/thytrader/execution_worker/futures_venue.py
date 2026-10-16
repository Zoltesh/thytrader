"""Production composition of the live futures broker and CFM evidence (ADR 0134 P2-7).

The worker's venue builder runs ``coinbase_futures_extension`` for every credentialed
generation. Spot books keep the plain spot broker; only live futures books are handed the
product-routed broker. Composition alone places nothing: a live futures start is a separate,
acknowledged path.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.exchanges.coinbase_cfm import CoinbaseCfmAccount
from thytrader.exchanges.coinbase_futures_broker import CoinbaseFuturesBroker
from thytrader.exchanges.rest_transport import RestClientTransport
from thytrader.execution.futures_live import FuturesLiveRuntime
from thytrader.execution.futures_sources import CatalogContractSizes, RecordedFuturesSource
from thytrader.execution.venue_routing import ProductRoutedBroker

if TYPE_CHECKING:
    from coinbase.rest import RESTClient

    from thytrader.exchanges.futures_models import FuturesAccountSnapshotStore
    from thytrader.execution.broker import Broker
    from thytrader.execution.futures_live import FuturesLiveSource
    from thytrader.execution.futures_paper import FuturesObservationReader
    from thytrader.execution_worker.venue import ExecutionVenue, VenueExtension


def compose_futures_venue(
    venue: ExecutionVenue,
    *,
    broker: Broker,
    source: FuturesLiveSource,
) -> ExecutionVenue:
    """Add a routed futures broker and evidence source; the spot broker stays unchanged."""
    if venue.live_broker is None:
        raise ValueError("Product routing also requires an explicit spot broker.")
    return replace(
        venue,
        futures_live_broker=ProductRoutedBroker(venue.live_broker, broker),
        futures_runtime=FuturesLiveRuntime(source),
    )


def coinbase_futures_extension(
    *,
    observations: FuturesObservationReader,
    mirror: FuturesAccountSnapshotStore | None,
) -> VenueExtension:
    """Build the Coinbase CFM broker and source on each generation's shared REST client."""

    def extend(venue: ExecutionVenue, client: RESTClient) -> ExecutionVenue:
        transport = RestClientTransport(client)
        return compose_futures_venue(
            venue,
            broker=CoinbaseFuturesBroker(transport, CatalogContractSizes(observations)),
            source=RecordedFuturesSource(
                CoinbaseCfmAccount(transport),
                CoinbaseAccount(client),
                observations,
                mirror=mirror,
            ),
        )

    return extend
