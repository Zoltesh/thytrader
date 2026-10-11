"""Explicit staged composition; never called by the production venue builder or CLI.

Callers supply adapters, not an approval boolean. Tests inject a synthetic venue here. Real
composition remains forbidden until the ADR 0134 venue probes and later activation gate land.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.execution.futures_live import FuturesLiveRuntime
from thytrader.execution.venue_routing import ProductRoutedBroker

if TYPE_CHECKING:
    from thytrader.execution.broker import Broker
    from thytrader.execution.futures_live import FuturesLiveSource
    from thytrader.execution_worker.venue import ExecutionVenue


def compose_futures_venue(
    venue: ExecutionVenue,
    *,
    broker: Broker,
    source: FuturesLiveSource,
) -> ExecutionVenue:
    """Inject a futures broker and evidence source without constructing or approving either."""
    if venue.live_broker is None:
        raise ValueError("Staged product routing also requires an explicit spot broker.")
    return replace(
        venue,
        live_broker=ProductRoutedBroker(venue.live_broker, broker),
        futures_runtime=FuturesLiveRuntime(source),
    )
