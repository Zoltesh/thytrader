"""Futures catalog and funding-rate poller for the market-data worker (ADR 0126, P0-3).

Every five minutes the poller reads the complete futures listing, records changed
contract facts and the listed funding rate per perp, and settles funding hours the
listing has moved past. It is read-only toward the venue (one public GET per page).
A failed poll records a failure code and leaves all history untouched; a settled-rate
conflict is written to the audit log and counted on the row, never rewritten.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING

from thytrader.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventUnavailableError,
)
from thytrader.market_data.futures_observations import (
    FUTURES_POLL_INTERVAL_SECONDS,
    FUTURES_PROVIDER,
    FuturesObservationUnavailableError,
    funding_samples,
    instrument_observation,
)
from thytrader.market_data.instrument_catalog import futures_catalog_fingerprint
from thytrader.market_data.instruments import InstrumentKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from thytrader.audit_events import AuditEventStore
    from thytrader.market_data.futures_observations import (
        FundingSample,
        FuturesObservationStore,
    )
    from thytrader.market_data.instruments import FuturesCatalogProvider

_logger = logging.getLogger(__name__)
LISTING_UNAVAILABLE = "FUTURES_LISTING_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class FuturesPollOutcome:
    """One poll's result for logs and tests."""

    succeeded: bool
    contract_count: int = 0
    funding_samples: int = 0
    inserted: int = 0
    settled: int = 0
    conflicts: int = 0
    failure_code: str | None = None


async def poll_futures_catalog_once(
    *,
    provider: FuturesCatalogProvider,
    store: FuturesObservationStore,
    audit_store: AuditEventStore | None,
    now: datetime,
) -> FuturesPollOutcome:
    """Read the listing once and record it; a listing failure records only the failure.

    ``ValueError`` covers the adapter's fail-closed listing errors; ``OSError`` covers
    anything the transport raised past it. A store failure propagates to the loop.
    """
    try:
        products = await provider.list_futures_products()
    except (OSError, ValueError) as error:
        _logger.warning("futures_catalog_poll_failed type=%s", type(error).__name__)
        await store.record_poll_failure(attempted_at=now, failure_code=LISTING_UNAVAILABLE)
        return FuturesPollOutcome(succeeded=False, failure_code=LISTING_UNAVAILABLE)
    samples = funding_samples(products)
    outcome = await store.record_poll(
        observed_at=now,
        observations=tuple(instrument_observation(product) for product in products),
        samples=samples,
        listing_fingerprint=futures_catalog_fingerprint(products),
        perpetual_count=sum(p.kind is InstrumentKind.PERPETUAL_FUTURE for p in products),
    )
    for conflict in outcome.conflicts:
        await _audit_conflict(audit_store, conflict, now)
    return FuturesPollOutcome(
        succeeded=True,
        contract_count=len(products),
        funding_samples=len(samples),
        inserted=outcome.inserted,
        settled=outcome.settled,
        conflicts=len(outcome.conflicts),
    )


async def run_futures_catalog_poller(
    stop_requested: asyncio.Event,
    *,
    provider: FuturesCatalogProvider | None,
    store: FuturesObservationStore,
    audit_store: AuditEventStore | None = None,
    interval_seconds: float = FUTURES_POLL_INTERVAL_SECONDS,
    now_factory: Callable[[], datetime] | None = None,
) -> None:
    """Poll until stopped; demo mode (no provider) records nothing and waits."""
    if provider is None:
        _logger.info("futures_catalog_poller_disabled reason=no_provider")
        await stop_requested.wait()
        return
    clock = now_factory or (lambda: datetime.now(UTC))
    while not stop_requested.is_set():
        try:
            outcome = await poll_futures_catalog_once(
                provider=provider, store=store, audit_store=audit_store, now=clock()
            )
        except FuturesObservationUnavailableError:
            _logger.warning("futures_catalog_store_unavailable")
        else:
            _logger.info(
                "futures_catalog_polled ok=%s contracts=%d samples=%d inserted=%d settled=%d "
                "conflicts=%d",
                outcome.succeeded,
                outcome.contract_count,
                outcome.funding_samples,
                outcome.inserted,
                outcome.settled,
                outcome.conflicts,
            )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_requested.wait(), timeout=interval_seconds)


async def _audit_conflict(
    audit_store: AuditEventStore | None, sample: FundingSample, now: datetime
) -> None:
    """Record a settled-rate conflict; the stored settled rate is not changed."""
    _logger.warning(
        "futures_funding_conflict product=%s funding_time=%s",
        sample.product_id,
        sample.funding_time.isoformat(),
    )
    if audit_store is None:
        return
    event = AuditEvent(
        occurred_at=now,
        category=AuditEventCategory.MARKET_DATA,
        action="futures_funding_conflict",
        outcome=AuditEventOutcome.FAILURE,
        detail=(
            f"A later listing reported rate {sample.rate} for the settled funding hour "
            f"{sample.funding_time.isoformat()}; the settled rate was kept."
        ),
        provider=FUTURES_PROVIDER,
        product_id=sample.product_id,
    )
    try:
        await audit_store.append(event)
    except AuditEventUnavailableError:
        _logger.warning("futures_funding_conflict_audit_unavailable")
