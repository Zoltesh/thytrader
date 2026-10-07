"""Hot-swappable Coinbase or demo venue bindings for the execution worker.

The execution worker used to build its Coinbase broker, market data, quote reader,
and user-feed JWT once at startup from environment settings, so credentials set
through ``thytrader-runtime set-coinbase-credentials`` or the Settings UI never
reached it. ``ExecutionVenueRuntime`` rebuilds those bindings when the shared
credentials volume changes. The worker reads one immutable ``ExecutionVenue``
at the start of each cycle, so a swap only takes effect between cycles and a
single cycle never mixes two credential generations.

Clearing credentials yields a venue with ``live_broker=None``; live deployments
then pause through the existing "Live broker is unavailable." path and never
evaluate demo candles for live orders.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import logging
from typing import TYPE_CHECKING, Literal

from coinbase.jwt_generator import build_ws_jwt
from coinbase.rest import RESTClient

from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.exchanges.coinbase_broker import CoinbaseRestBroker
from thytrader.exchanges.coinbase_market_data import CoinbaseMarketData
from thytrader.exchanges.rest_transport import RestClientTransport
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService

if TYPE_CHECKING:
    from thytrader.config import Settings
    from thytrader.execution.broker import Broker
    from thytrader.execution_worker.ports import QuoteBalanceReader

_logger = logging.getLogger(__name__)

VenueSource = Literal["coinbase", "demo"]


@dataclass(frozen=True, slots=True)
class ExecutionVenue:
    """One immutable credential generation of execution-worker venue bindings.

    Attributes:
        market_data: Candle source for paper and live evaluation.
        live_broker: Coinbase broker, or None when credentials are absent.
        quote_reader: Coinbase balance reader, or None without credentials.
        jwt_provider: Builds a short-lived user-feed JWT, or None without credentials.
        source: ``coinbase`` for venue candles, ``demo`` for synthetic candles.
        generation: Monotonic counter; increments on every credential change.
    """

    market_data: MarketDataService
    live_broker: Broker | None
    quote_reader: QuoteBalanceReader | None
    jwt_provider: Callable[[], str] | None
    source: VenueSource
    generation: int

    @property
    def live_enabled(self) -> bool:
        """True when this generation can submit live orders."""
        return self.live_broker is not None


VenueBuilder = Callable[["Settings", int], ExecutionVenue]


def build_execution_venue(settings: Settings, generation: int) -> ExecutionVenue:
    """Use Coinbase REST when credentials exist, otherwise demo candles and no live broker."""
    key = settings.coinbase_api_key_name
    secret = settings.coinbase_api_private_key
    if key is None or secret is None:
        return ExecutionVenue(
            market_data=MarketDataService(DemoMarketData()),
            live_broker=None,
            quote_reader=None,
            jwt_provider=None,
            source="demo",
            generation=generation,
        )
    key_name = key.get_secret_value()
    private_key = secret.get_secret_value()
    client = RESTClient(api_key=key_name, api_secret=private_key, timeout=10)
    return ExecutionVenue(
        market_data=MarketDataService(CoinbaseMarketData(client)),
        live_broker=CoinbaseRestBroker(RestClientTransport(client)),
        quote_reader=CoinbaseAccount(client),
        jwt_provider=lambda: build_ws_jwt(key_name, private_key),
        source="coinbase",
        generation=generation,
    )


def credential_identity(settings: Settings) -> str | None:
    """Return a SHA-256 identity of the Coinbase secret pair, or None when absent.

    Only the digest is kept for comparison so unrelated dotenv edits do not
    rebuild clients or reconnect the user feed.
    """
    key = settings.coinbase_api_key_name
    secret = settings.coinbase_api_private_key
    if key is None or secret is None:
        return None
    digest = hashlib.sha256()
    digest.update(key.get_secret_value().encode("utf-8"))
    digest.update(b"\0")
    digest.update(secret.get_secret_value().encode("utf-8"))
    return digest.hexdigest()


class ExecutionVenueRuntime:
    """Hold the current venue generation and rebuild it when credentials change."""

    def __init__(
        self,
        settings: Settings,
        *,
        builder: VenueBuilder = build_execution_venue,
    ) -> None:
        """Bind the first venue generation from startup settings."""
        self._builder = builder
        self._identity = credential_identity(settings)
        self._venue = builder(settings, 0)

    def current(self) -> ExecutionVenue:
        """Return the venue generation the next worker cycle must use."""
        return self._venue

    def replace(self, settings: Settings) -> None:
        """Rebuild venue bindings when the Coinbase secret pair actually changed.

        Callers invoke this from the credential reload callback. The previous
        venue object stays valid for any cycle already holding it.
        """
        identity = credential_identity(settings)
        if identity == self._identity:
            return
        generation = self._venue.generation + 1
        venue = self._builder(settings, generation)
        self._identity = identity
        self._venue = venue
        _logger.info(
            "execution_venue_reloaded generation=%s source=%s live_enabled=%s",
            generation,
            venue.source,
            venue.live_enabled,
        )


def venue_transition_detail(venue: ExecutionVenue) -> str:
    """Describe one venue generation for audit without secret material."""
    if venue.live_enabled:
        return (
            f"generation={venue.generation} source=coinbase: live broker, Coinbase candles, "
            "and user-order feed use the current shared credentials."
        )
    return (
        f"generation={venue.generation} source=demo: no Coinbase credentials. Live "
        "deployments pause (Live broker is unavailable.). Paper deployments evaluate "
        "synthetic demo candles, not venue prices."
    )
