"""The GET-only CFM evidence source a live futures start is admitted on (ADR 0134 P2-7).

The API never builds a futures broker; it reads the CFM balance, positions and open futures
orders once per live start, from the credentials current at that request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from coinbase.rest import RESTClient

from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.exchanges.coinbase_cfm import CoinbaseCfmAccount
from thytrader.exchanges.rest_transport import RestClientTransport
from thytrader.execution.futures_sources import RecordedFuturesSource

if TYPE_CHECKING:
    from thytrader.config import Settings
    from thytrader.exchanges.futures_models import FuturesAccountSnapshotStore
    from thytrader.execution.futures_paper import FuturesObservationReader


def live_futures_start_source(
    settings: Settings,
    *,
    observations: FuturesObservationReader,
    mirror: FuturesAccountSnapshotStore | None,
) -> RecordedFuturesSource | None:
    """A CFM source on the current credentials, or None without them (evidence unknown)."""
    key = settings.coinbase_api_key_name
    secret = settings.coinbase_api_private_key
    if key is None or secret is None:
        return None
    client = RESTClient(
        api_key=key.get_secret_value(), api_secret=secret.get_secret_value(), timeout=10
    )
    return RecordedFuturesSource(
        CoinbaseCfmAccount(RestClientTransport(client)),
        CoinbaseAccount(client),
        observations,
        mirror=mirror,
    )
