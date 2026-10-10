"""Operator ``funding`` report: poller health, coverage, gaps and conflicts (ADR 0126)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from thytrader.market_data.futures_observations import (
    FundingRateRecord,
    FundingRecordOutcome,
    FundingSample,
    FuturesInstrumentObservation,
    FuturesObservationUnavailableError,
    FuturesPollState,
)
from thytrader.operator.funding_report import FundingReport, build_funding_report
from thytrader.operator.models import ReportStatus

_NOW = datetime(2026, 10, 10, 12, 30, tzinfo=UTC)
_DAY = datetime(2026, 10, 10, tzinfo=UTC)
_BIP = "BIP-20DEC30-CDE"
_US5 = "US5-19DEC30-CDE"


def _record(
    hour: int, *, settled: bool = True, conflicts: int = 0, product_id: str = _BIP
) -> FundingRateRecord:
    """One stored hour of funding (BIP by default)."""
    funding_time = _DAY + timedelta(hours=hour)
    return FundingRateRecord(
        product_id=product_id,
        funding_time=funding_time,
        rate=Decimal("0.000009"),
        interval_seconds=3600,
        first_observed_at=funding_time + timedelta(minutes=5),
        last_observed_at=funding_time + timedelta(minutes=55),
        observation_count=11,
        revision_count=0,
        settled=settled,
        settled_at=funding_time + timedelta(hours=1) if settled else None,
        conflict_count=conflicts,
        last_conflict_rate=Decimal("0.1") if conflicts else None,
        last_conflict_at=funding_time + timedelta(hours=2) if conflicts else None,
    )


class _Store:
    """Serve fixed poll state and history, or fail like an unreachable database."""

    def __init__(
        self,
        records: tuple[FundingRateRecord, ...],
        *,
        state: FuturesPollState | None,
        fail: bool = False,
    ) -> None:
        """Hold the canned answers."""
        self.records = records
        self.state = state
        self.fail = fail

    async def record_poll(
        self,
        *,
        observed_at: datetime,
        observations: tuple[FuturesInstrumentObservation, ...],
        samples: tuple[FundingSample, ...],
        listing_fingerprint: str,
        perpetual_count: int,
    ) -> FundingRecordOutcome:
        """Unused by the report."""
        del observed_at, observations, samples, listing_fingerprint, perpetual_count
        raise AssertionError("the report is read-only")

    async def record_poll_failure(self, *, attempted_at: datetime, failure_code: str) -> None:
        """Unused by the report."""
        del attempted_at, failure_code
        raise AssertionError("the report is read-only")

    async def poll_state(self) -> FuturesPollState | None:
        """Return the canned poll state."""
        if self.fail:
            raise FuturesObservationUnavailableError("down")
        return self.state

    async def funding_rates(
        self, *, product_id: str | None, starts_at: datetime, ends_at: datetime
    ) -> tuple[FundingRateRecord, ...]:
        """Filter the canned history like the SQL store."""
        return tuple(
            r
            for r in self.records
            if starts_at <= r.funding_time < ends_at
            and (product_id is None or r.product_id == product_id)
        )

    async def trades_around_the_clock(self) -> dict[str, bool]:
        """BIP trades 24/7; a session-limited index perp does not."""
        return {_BIP: True, _US5: False}

    async def funding_history_starts(self) -> dict[str, datetime]:
        """First stored hour per contract."""
        starts: dict[str, datetime] = {}
        for record in sorted(self.records, key=lambda r: r.funding_time):
            starts.setdefault(record.product_id, record.funding_time)
        return starts


def _state(last_success: datetime | None = _NOW - timedelta(minutes=3)) -> FuturesPollState:
    """A recent successful poll by default."""
    return FuturesPollState(
        provider="coinbase",
        last_attempt_at=_NOW - timedelta(minutes=3),
        last_success_at=last_success,
        consecutive_failures=0 if last_success else 4,
        failure_code=None if last_success else "FUTURES_LISTING_UNAVAILABLE",
        listing_fingerprint="sha256:" + "a" * 64,
        contract_count=100,
        perpetual_count=29,
    )


def _build(store: _Store, product_id: str | None = None) -> FundingReport:
    """Build the report at the fixed clock."""
    return asyncio.run(build_funding_report(store, product_id=product_id, hours=24, now=_NOW))


def test_complete_history_is_healthy_and_the_current_hour_is_not_a_gap() -> None:
    """Hours 0..11 settled and 12 current: no gaps, healthy."""
    records = (*(_record(hour) for hour in range(12)), _record(12, settled=False))
    report = _build(_Store(records, state=_state()))
    assert report.overall_status is ReportStatus.HEALTHY
    (contract,) = report.payload.contracts
    assert contract.latest_funding_time == _DAY + timedelta(hours=12)
    assert contract.latest_settled is False
    assert (contract.stored_hours, contract.settled_hours, contract.gap_hours) == (13, 12, 0)
    assert report.payload.rows == ()
    assert report.payload.window_ends_at == _DAY + timedelta(hours=13)


def test_missing_hour_is_a_gap_and_rows_are_listed_for_one_contract() -> None:
    """A missing hour degrades the report; a scoped report lists every stored hour."""
    records = tuple(_record(hour) for hour in range(12) if hour != 5)
    report = _build(_Store(records, state=_state()), product_id=_BIP)
    assert report.overall_status is ReportStatus.DEGRADED
    (contract,) = report.payload.contracts
    assert contract.gap_times == (_DAY + timedelta(hours=5),)
    assert len(report.payload.rows) == 11
    assert report.components[1].reason_code == "FUNDING_HISTORY_GAPS"


def test_conflicts_and_stale_poller_are_named() -> None:
    """A settled-rate conflict and a stale poller each have their own reason code."""
    records = (_record(0, conflicts=2),)
    report = _build(_Store(records, state=_state(last_success=_NOW - timedelta(hours=2))))
    codes = {component.reason_code for component in report.components}
    assert codes == {"FUTURES_POLLER_STALE", "FUNDING_RATE_CONFLICT"}
    assert report.payload.contracts[0].conflict_count == 2
    assert report.payload.poller is not None
    assert report.payload.poller.stale is True


def test_unrun_poller_and_unavailable_store_are_truthful() -> None:
    """No poll yet is DEGRADED; a storage failure is FAILED with an empty payload."""
    unrun = _build(_Store((), state=None))
    assert unrun.overall_status is ReportStatus.DEGRADED
    assert unrun.components[0].reason_code == "FUTURES_POLLER_NOT_RUN"
    failed = _build(_Store((), state=None, fail=True))
    assert failed.overall_status is ReportStatus.FAILED
    assert failed.payload.contracts == ()
    disabled = asyncio.run(build_funding_report(None, product_id=None, hours=24, now=_NOW))
    assert disabled.components[0].reason_code == "STORE_DISABLED"


def test_report_round_trips_through_its_json_contract() -> None:
    """The HTTP CLI validates the same model it serves."""
    report = _build(_Store((_record(0),), state=_state()), product_id=_BIP)
    again = FundingReport.model_validate_json(report.model_dump_json())
    assert again == report


def test_session_limited_gaps_are_reported_without_degrading() -> None:
    """Closed-session hours of an index perp are listed but do not degrade the report."""
    records = (
        *(_record(hour) for hour in range(12)),
        _record(0, product_id=_US5),
        _record(9, product_id=_US5),
    )
    report = _build(_Store(records, state=_state()))
    assert report.overall_status is ReportStatus.HEALTHY
    us5 = next(c for c in report.payload.contracts if c.product_id == _US5)
    assert us5.twenty_four_by_seven is False
    assert us5.gap_hours == 10
