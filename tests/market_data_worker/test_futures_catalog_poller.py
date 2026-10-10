"""Futures catalog and funding poller (ADR 0126, P0-3) against in-memory fakes."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from thytrader.exchanges.coinbase_futures_catalog import (
    CoinbaseFuturesCatalogError,
    parse_futures_row,
)
from thytrader.market_data.futures_observations import (
    FundingRateRecord,
    FundingRecordOutcome,
    FundingSample,
    FuturesInstrumentObservation,
    FuturesPollState,
    funding_gaps,
    funding_samples,
    instrument_observation,
)
from thytrader.market_data_worker.futures_catalog import (
    LISTING_UNAVAILABLE,
    poll_futures_catalog_once,
    run_futures_catalog_poller,
)

if TYPE_CHECKING:
    from thytrader.audit_events import AuditEvent
    from thytrader.market_data.instruments import FuturesProduct

_FIXTURE = Path(__file__).parents[1] / "exchanges" / "fixtures" / "coinbase_futures_listing.json"
_NOW = datetime(2026, 10, 10, 0, 48, tzinfo=UTC)


def _products() -> tuple[FuturesProduct, ...]:
    """Parse the verbatim fixture's FCM rows."""
    payload: dict[str, Any] = json.loads(_FIXTURE.read_text())
    parsed = (parse_futures_row(row) for row in payload["products"])
    return tuple(product for product in parsed if product is not None)


class _Provider:
    """Return the fixture listing, or fail like the adapter's fail-closed error."""

    def __init__(self, *, fail: bool = False) -> None:
        """Choose success or failure."""
        self.fail = fail
        self.calls = 0

    async def list_futures_products(self) -> tuple[FuturesProduct, ...]:
        """One listing read."""
        self.calls += 1
        if self.fail:
            raise CoinbaseFuturesCatalogError("listing unavailable")
        return _products()


class _Store:
    """Record what the poller hands to storage."""

    def __init__(self, conflicts: tuple[FundingSample, ...] = ()) -> None:
        """Optionally report conflicts back to the poller."""
        self.conflicts = conflicts
        self.polls: list[dict[str, Any]] = []
        self.polled = asyncio.Event()
        self.failures: list[tuple[datetime, str]] = []

    async def record_poll(
        self,
        *,
        observed_at: datetime,
        observations: tuple[FuturesInstrumentObservation, ...],
        samples: tuple[FundingSample, ...],
        listing_fingerprint: str,
        perpetual_count: int,
    ) -> FundingRecordOutcome:
        """Capture one successful poll."""
        self.polled.set()
        self.polls.append(
            {
                "observed_at": observed_at,
                "observations": observations,
                "samples": samples,
                "listing_fingerprint": listing_fingerprint,
                "perpetual_count": perpetual_count,
            }
        )
        return FundingRecordOutcome(inserted=len(samples), settled=0, conflicts=self.conflicts)

    async def record_poll_failure(self, *, attempted_at: datetime, failure_code: str) -> None:
        """Capture one failed poll."""
        self.failures.append((attempted_at, failure_code))

    async def poll_state(self) -> FuturesPollState | None:
        """Unused by the poller."""
        return None

    async def funding_rates(
        self, *, product_id: str | None, starts_at: datetime, ends_at: datetime
    ) -> tuple[FundingRateRecord, ...]:
        """Unused by the poller."""
        del product_id, starts_at, ends_at
        return ()

    async def funding_history_starts(self) -> dict[str, datetime]:
        """Unused by the poller."""
        return {}

    async def trades_around_the_clock(self) -> dict[str, bool]:
        """Unused by the poller."""
        return {}


class _Audit:
    """Collect audit events."""

    def __init__(self) -> None:
        """Start empty."""
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> None:
        """Capture one event."""
        self.events.append(event)

    async def list_recent(self, *, limit: int = 50) -> tuple[AuditEvent, ...]:
        """Return captured events newest first."""
        return tuple(reversed(self.events))[:limit]


def test_poll_records_every_contract_and_one_sample_per_listed_perp() -> None:
    """Eight contracts are observed; the four perps contribute funding samples."""
    store = _Store()
    outcome = asyncio.run(
        poll_futures_catalog_once(provider=_Provider(), store=store, audit_store=None, now=_NOW)
    )
    assert outcome.succeeded
    assert (outcome.contract_count, outcome.funding_samples) == (8, 4)
    (poll,) = store.polls
    assert poll["perpetual_count"] == 4
    assert poll["observed_at"] == _NOW
    bip = next(s for s in poll["samples"] if s.product_id == "BIP-20DEC30-CDE")
    assert bip.rate == Decimal("0.000009")
    assert bip.interval_seconds == 3600


def test_listing_failure_records_only_a_failure_code() -> None:
    """A fail-closed listing writes no history, only the poll failure."""
    store = _Store()
    outcome = asyncio.run(
        poll_futures_catalog_once(
            provider=_Provider(fail=True), store=store, audit_store=None, now=_NOW
        )
    )
    assert not outcome.succeeded
    assert store.polls == []
    assert store.failures == [(_NOW, LISTING_UNAVAILABLE)]


def test_conflicts_are_audited_and_not_rewritten() -> None:
    """A conflict reported by storage becomes a market-data audit failure event."""
    conflict = FundingSample(
        product_id="BIP-20DEC30-CDE",
        funding_time=_NOW - timedelta(hours=2),
        rate=Decimal("-0.0001"),
        interval_seconds=3600,
    )
    audit = _Audit()
    outcome = asyncio.run(
        poll_futures_catalog_once(
            provider=_Provider(), store=_Store((conflict,)), audit_store=audit, now=_NOW
        )
    )
    assert outcome.conflicts == 1
    (event,) = audit.events
    assert event.action == "futures_funding_conflict"
    assert event.product_id == "BIP-20DEC30-CDE"
    assert "settled rate was kept" in event.detail


def test_runner_polls_until_stopped_and_is_idle_without_a_provider() -> None:
    """The loop polls at once; demo mode never reads a listing."""

    async def exercise() -> None:
        """Run one poll, stop, and run a provider-less loop."""
        stop = asyncio.Event()
        store = _Store()
        provider = _Provider()
        task = asyncio.create_task(
            run_futures_catalog_poller(
                stop, provider=provider, store=store, interval_seconds=60, now_factory=lambda: _NOW
            )
        )
        await store.polled.wait()
        stop.set()
        await task
        assert provider.calls == 1
        idle_store = _Store()
        await run_futures_catalog_poller(stop, provider=None, store=idle_store)
        assert idle_store.polls == []

    asyncio.run(exercise())


def test_funding_samples_never_record_an_unknown_rate() -> None:
    """A perp listed without a rate contributes no sample (never a zero)."""
    products = list(_products())
    index = next(i for i, p in enumerate(products) if p.product_id == "ETP-20DEC30-CDE")
    funding = products[index].funding
    assert funding is not None
    products[index] = replace(products[index], funding=replace(funding, rate=None))
    ids = {sample.product_id for sample in funding_samples(products)}
    assert "ETP-20DEC30-CDE" not in ids
    assert "BIP-20DEC30-CDE" in ids


def test_observation_fingerprint_ignores_funding_but_tracks_margin() -> None:
    """Hourly funding does not create rows; a margin change does."""
    bip = next(p for p in _products() if p.product_id == "BIP-20DEC30-CDE")
    base = instrument_observation(bip)
    assert bip.funding is not None
    new_rate = replace(bip, funding=replace(bip.funding, rate=Decimal("0.5")))
    assert instrument_observation(new_rate).payload_fingerprint == base.payload_fingerprint
    assert (
        replace(base, overnight_long_margin_rate="0.5").payload_fingerprint
        != base.payload_fingerprint
    )
    assert base.underlying == "BTC"
    assert base.funding_interval_seconds == 3600


def _record(hour: int) -> FundingRateRecord:
    """A stored settled hour at ``_NOW``'s day plus ``hour``."""
    funding_time = datetime(2026, 10, 10, tzinfo=UTC) + timedelta(hours=hour)
    return FundingRateRecord(
        product_id="BIP-20DEC30-CDE",
        funding_time=funding_time,
        rate=Decimal("0.00001"),
        interval_seconds=3600,
        first_observed_at=funding_time,
        last_observed_at=funding_time,
        observation_count=1,
        revision_count=0,
        settled=True,
        settled_at=funding_time + timedelta(hours=1),
        conflict_count=0,
        last_conflict_rate=None,
        last_conflict_at=None,
    )


def test_gaps_start_at_the_first_recorded_hour() -> None:
    """Hours before history began are not gaps; a missing middle hour is."""
    day = datetime(2026, 10, 10, tzinfo=UTC)
    gaps = funding_gaps(
        (_record(2), _record(4)),
        history_starts_at=day + timedelta(hours=2),
        starts_at=day - timedelta(hours=5),
        ends_at=day + timedelta(hours=5),
        interval=timedelta(hours=1),
    )
    assert gaps == (day + timedelta(hours=3),)
    assert (
        funding_gaps(
            (),
            history_starts_at=None,
            starts_at=day,
            ends_at=day + timedelta(hours=5),
            interval=timedelta(hours=1),
        )
        == ()
    )
