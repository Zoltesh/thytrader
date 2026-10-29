"""The live CFM fleet pass judges probe freshness after its own reads (ADR 0115).

A receipt the fleet pass read itself (the cycle had no shared one: the observer's read failed,
or a book started between the two inventory reads) is newer than the pass start. Judged at the
pass start it read as future-dated, so a flat live book raised a one-cycle critical
``FLEET_ENTRIES_BLOCKED``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, cast

import pytest

from tests.operator_diagnostics.test_futures_books_report import _AT
from tests.operator_diagnostics.test_futures_visibility_proofs import _probe, _probe_inputs
from thytrader.execution import fleet_entry_evidence, futures_visibility_fleet
from thytrader.execution.futures_visibility_fleet import load_live_futures_probes
from thytrader.risk.breakers import FUTURES_SCOPE, EntryObservation
from thytrader.risk.fleet_entry_health import evaluate_fleet_entry_health
from thytrader.risk.fleet_entry_models import EVIDENCE_REASON_CODES, FleetScopeEvidence
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from collections.abc import Mapping

    from thytrader.execution.futures_paper import FuturesRuntime
    from thytrader.risk.futures_live import FuturesVenueEvidence
    from thytrader.trading.models import DeploymentSnapshot

_RECEIVED = _AT + timedelta(seconds=2)
_JUDGED = _AT + timedelta(seconds=3)


def _live_futures_blocks(
    *,
    venue: FuturesVenueEvidence | None,
    judged_at: datetime | None,
    funding_rate: Decimal | None = Decimal("0.0001"),
    futures_caps: Mapping[str, object] | None = None,
) -> set[str]:
    """Reason codes of the scope's ``live_futures`` checks for one flat live book."""
    book, policy, _venue = _probe_inputs()
    if futures_caps:
        assert policy.futures is not None
        policy = policy.model_copy(
            update={"futures": policy.futures.model_copy(update=dict(futures_caps))}
        )
    evidence = FleetScopeEvidence(
        mode=DeploymentMode.LIVE,
        scope=FUTURES_SCOPE,
        probe_product=book.deployment.product_id,
        live_futures_venue=venue,
        live_futures_probes={book.deployment.id: _probe(book, None, funding_rate=funding_rate)},
        live_futures_judged_at=judged_at,
    )
    (scope,) = evaluate_fleet_entry_health(
        policy,
        snapshots=(book,),
        evidence=(evidence,),
        observation=EntryObservation(
            as_of=_AT, proposed_price=None, reference_price=None, marks={}
        ),
    )
    return {
        check.reason_code or ""
        for check in scope.checks
        if check.name == "live_futures" and check.status == "blocked"
    }


def test_a_receipt_read_during_the_pass_is_judged_after_it() -> None:
    """The verdict uses the judged time; the pass-start stamp would call it future-dated."""
    _book, _policy, venue = _probe_inputs()
    received = replace(venue, observed_at=_RECEIVED)
    assert _live_futures_blocks(venue=received, judged_at=_JUDGED) == set()
    # The defect this fixes: judged at the pass start, the same receipt blocks entries.
    assert _live_futures_blocks(venue=received, judged_at=None)


def test_a_missing_settled_funding_rate_blocks_the_live_scope_as_missing_evidence() -> None:
    """Execution denies a live entry without a settled funding rate; so must fleet health."""
    _book, _policy, venue = _probe_inputs()
    assert _live_futures_blocks(venue=venue, judged_at=_AT) == set()
    assert _live_futures_blocks(venue=venue, judged_at=_AT, funding_rate=None) == {
        "FUNDING_HISTORY_MISSING"
    }
    assert "FUNDING_HISTORY_MISSING" in EVIDENCE_REASON_CODES


def test_the_policys_futures_caps_reach_the_live_fleet_probe() -> None:
    """A funding rate over the policy cap or a tiny order cap blocks the scope, as at the gate."""
    _book, _policy, venue = _probe_inputs()
    over: Mapping[str, object] = {"max_hourly_funding_rate_abs": "0.00005"}
    assert _live_futures_blocks(venue=venue, judged_at=_AT, futures_caps=over) == {
        "FUTURES_FUNDING_RATE_EXCEEDED"
    }
    assert (
        _live_futures_blocks(
            venue=venue, judged_at=_AT, futures_caps={"max_hourly_funding_rate_abs": "0.001"}
        )
        == set()
    )


def test_a_capacity_cap_never_hides_missing_account_evidence() -> None:
    """With no account evidence the probe reports the evidence block, not the funding cap."""
    over: Mapping[str, object] = {"max_hourly_funding_rate_abs": "0.00005"}
    blocks = _live_futures_blocks(venue=None, judged_at=_AT, futures_caps=over)
    assert blocks
    assert "FUTURES_FUNDING_RATE_EXCEEDED" not in blocks
    assert blocks <= EVIDENCE_REASON_CODES


def test_judging_later_never_ages_stale_evidence_in_its_favour() -> None:
    """A receipt already stale at the judged time still blocks."""
    _book, _policy, venue = _probe_inputs()
    stale = replace(venue, observed_at=_AT - timedelta(minutes=10))
    assert _live_futures_blocks(venue=stale, judged_at=_JUDGED)


@dataclass
class _Account:
    """Venue evidence whose freshness checks are recorded."""

    venue: object
    marks: dict[str, Decimal] = field(default_factory=dict)


@dataclass
class _Venue:
    events: list[str]
    judged: list[datetime] = field(default_factory=list)

    def is_fresh(self, as_of: datetime) -> bool:
        self.judged.append(as_of)
        return True


@dataclass
class _Contracts:
    events: list[str]

    async def load_contract(self, deployment_id: object) -> None:
        del deployment_id
        self.events.append("binding")


@dataclass
class _Observations:
    events: list[str]

    async def latest_instrument(self, product_id: str) -> None:
        del product_id
        self.events.append("instrument")


@dataclass
class _Runtime:
    contracts: _Contracts
    observations: _Observations
    margin_max_age: timedelta | None = None


async def _load(
    monkeypatch: pytest.MonkeyPatch, *, clock: bool
) -> tuple[list[str], _Venue, list[datetime], datetime]:
    """Run the loader with recording reads; return events, venue, probe times, judged time."""
    events: list[str] = []
    venue = _Venue(events)
    probed: list[datetime] = []

    async def prepare(book: DeploymentSnapshot, *, portfolio: object) -> _Account:
        del book, portfolio
        events.append("account")
        return _Account(venue)

    def probe(book: DeploymentSnapshot, *, now: datetime, **kwargs: object) -> object:
        del kwargs
        probed.append(now)
        return _probe(book, None)

    def read_clock() -> datetime:
        events.append("clock")
        return _JUDGED

    monkeypatch.setattr(futures_visibility_fleet, "current_live_futures_runtime", object)
    monkeypatch.setattr(futures_visibility_fleet, "prepare_live_futures_book", prepare)
    monkeypatch.setattr(futures_visibility_fleet, "visibility_probe", probe)
    book, _policy, _venue = _probe_inputs()
    _evidence, probes, judged = await load_live_futures_probes(
        (book,),
        store=None,
        runtime=cast("FuturesRuntime", _Runtime(_Contracts(events), _Observations(events))),
        now=_AT,
        clock=read_clock if clock else None,
    )
    assert set(probes) == {book.deployment.id}
    return events, venue, probed, judged


@pytest.mark.anyio
async def test_the_loader_reads_everything_before_it_takes_the_judged_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Account, binding and instrument reads all come before the clock is read."""
    events, venue, probed, judged = await _load(monkeypatch, clock=True)
    assert events == ["account", "binding", "instrument", "clock"]
    assert judged == _JUDGED
    assert venue.judged == [_JUDGED]
    assert probed == [_JUDGED]


@pytest.mark.anyio
async def test_without_a_clock_the_loader_judges_at_the_given_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reports and tests that pass a fixed time keep it."""
    events, venue, probed, judged = await _load(monkeypatch, clock=False)
    assert "clock" not in events
    assert judged == _AT
    assert venue.judged == [_AT]
    assert probed == [_AT]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["worker", "fixed", "report"])
async def test_the_fleet_pass_hands_the_probe_loader_its_judging_clock(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """The worker judges on its clock, a fixed ``now`` stays fixed, a report reads utc_now."""
    book, policy, _venue = _probe_inputs()
    store = InMemoryExecutionStore()
    await store.create_deployment(book.deployment)
    seen: list[object] = []

    async def loader(snapshots: object, *, clock: object, **kwargs: object) -> tuple:
        del snapshots, kwargs
        seen.append(clock)
        return None, {}, _AT

    def worker_clock() -> datetime:
        return _JUDGED

    def report_clock() -> datetime:
        return _AT

    monkeypatch.setattr(fleet_entry_evidence, "load_live_futures_probes", loader)
    monkeypatch.setattr(fleet_entry_evidence, "utc_now", report_clock)
    load = fleet_entry_evidence.load_fleet_entry_health
    if mode == "worker":
        health = await load(
            store, policy, market_data=None, futures_account=None, now=_AT, clock=worker_clock
        )
    elif mode == "fixed":
        health = await load(store, policy, market_data=None, futures_account=None, now=_AT)
    else:
        health = await load(store, policy, market_data=None, futures_account=None)
    assert health.evaluated_at == _AT
    assert seen == [{"worker": worker_clock, "fixed": None, "report": report_clock}[mode]]


@pytest.mark.anyio
async def test_the_probe_loads_a_perps_settled_funding_rate_like_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fleet probe reads the same newest settled rate execution reads for a live perp."""
    book, _policy, _venue = _probe_inputs()
    binding = _probe(book, None).state.binding
    assert binding is not None
    assert binding.contract.kind == "perpetual_future"
    asked: list[str] = []

    async def rate(runtime: object, product_id: str, now: datetime) -> Decimal:
        del runtime, now
        asked.append(product_id)
        return Decimal("0.0002")

    @dataclass
    class _Bound:
        async def load_contract(self, deployment_id: object) -> object:
            del deployment_id
            return binding

    @dataclass
    class _Catalog:
        async def latest_instrument(self, product_id: str) -> None:
            del product_id

    monkeypatch.setattr(futures_visibility_fleet, "latest_settled_rate", rate)
    runtime = cast(
        "FuturesRuntime", _Runtime(cast("_Contracts", _Bound()), cast("_Observations", _Catalog()))
    )
    read = await futures_visibility_fleet._probe_inputs(runtime, book, _AT)
    assert read is not None
    assert read[4] == Decimal("0.0002")
    assert asked == [book.deployment.product_id]
