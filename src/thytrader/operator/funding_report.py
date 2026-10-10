"""Operator ``funding`` report: recorded CFM funding history and poller health (ADR 0126).

Read-only. It reports what the market-data worker's futures poller recorded: poller
status, per-contract coverage (first recorded hour, settled hours, gaps, conflicts) and,
for one contract, every stored hour in the window. Rates are exact decimal strings per
funding interval (hourly on CFM; longs pay when positive). A gap is an hour after the
contract's first recorded hour with no stored rate; it is never filled or assumed zero.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from thytrader import __version__
from thytrader.market_data.futures_observations import (
    FUTURES_POLL_INTERVAL_SECONDS,
    FuturesObservationUnavailableError,
    funding_gaps,
)
from thytrader.operator.models import (
    STANDARD_REDACTION,
    ComponentReport,
    OperatorEnvelope,
    ReportStatus,
    _FrozenModel,
)
from thytrader.operator.status import aggregate_status, recommend_next_action

if TYPE_CHECKING:
    from thytrader.market_data.futures_observations import (
        FundingRateRecord,
        FuturesObservationStore,
        FuturesPollState,
    )

FUNDING_REPORT_MAX_HOURS = 720
FUNDING_REPORT_DEFAULT_HOURS = 24
_MAX_GAP_TIMES = 24
# A poller that has not succeeded for three intervals is stale.
_STALE_AFTER = timedelta(seconds=3 * FUTURES_POLL_INTERVAL_SECONDS)
_HOUR = timedelta(hours=1)


class FundingPollerSummary(_FrozenModel):
    """The futures poller's last attempt and success."""

    last_attempt_at: datetime
    last_success_at: datetime | None
    consecutive_failures: int
    failure_code: str | None
    contract_count: int | None
    perpetual_count: int | None
    stale: bool


class FundingContractSummary(_FrozenModel):
    """Funding coverage for one perp-style contract in the report window.

    ``latest_rate`` is the newest stored hour's rate; ``latest_settled`` is false while
    the listing still names that hour. ``gap_times`` lists at most 24 missing hours.
    ``twenty_four_by_seven`` is false for a contract with trading sessions (index perps):
    its closed-session hours show as gaps but do not degrade the report; ``null`` means no
    contract observation was recorded.
    """

    product_id: str
    history_starts_at: datetime | None
    latest_funding_time: datetime | None
    latest_rate: str | None
    latest_settled: bool | None
    twenty_four_by_seven: bool | None
    stored_hours: int
    settled_hours: int
    gap_hours: int
    gap_times: tuple[datetime, ...]
    revision_count: int
    conflict_count: int


class FundingRateRow(_FrozenModel):
    """One stored funding hour (only when the report is scoped to one contract)."""

    funding_time: datetime
    rate: str
    interval_seconds: int
    settled: bool
    observation_count: int
    revision_count: int
    conflict_count: int
    last_conflict_rate: str | None
    first_observed_at: datetime
    last_observed_at: datetime


class FundingPayload(_FrozenModel):
    """Funding history visible to agents; no account data is involved."""

    window_starts_at: datetime
    window_ends_at: datetime
    hours: int = Field(ge=1, le=FUNDING_REPORT_MAX_HOURS)
    product_id: str | None
    poller: FundingPollerSummary | None
    contracts: tuple[FundingContractSummary, ...]
    rows: tuple[FundingRateRow, ...]


class FundingReport(OperatorEnvelope):
    """Read-only CFM funding-rate history and poller health."""

    report_kind: Literal["funding"] = "funding"
    payload: FundingPayload


async def build_funding_report(
    store: FuturesObservationStore | None,
    *,
    product_id: str | None,
    hours: int,
    now: datetime | None = None,
) -> FundingReport:
    """Assemble the funding report; an unavailable store is a FAILED report."""
    generated_at = now or datetime.now(UTC)
    window_ends_at = generated_at.replace(minute=0, second=0, microsecond=0) + _HOUR
    window_starts_at = window_ends_at - timedelta(hours=hours)
    empty = FundingPayload(
        window_starts_at=window_starts_at,
        window_ends_at=window_ends_at,
        hours=hours,
        product_id=product_id,
        poller=None,
        contracts=(),
        rows=(),
    )
    if store is None:
        return _report(generated_at, empty, (_component(ReportStatus.DEGRADED, "STORE_DISABLED"),))
    try:
        state = await store.poll_state()
        records = await store.funding_rates(
            product_id=product_id, starts_at=window_starts_at, ends_at=window_ends_at
        )
        starts = await store.funding_history_starts()
        around_the_clock = await store.trades_around_the_clock()
    except FuturesObservationUnavailableError:
        return _report(generated_at, empty, (_component(ReportStatus.FAILED, "STORE_UNAVAILABLE"),))
    poller = None if state is None else _poller(state, generated_at)
    judged_until = _judged_until(state)
    contracts = _contracts(
        records, starts, around_the_clock, product_id, window_starts_at, judged_until
    )
    payload = empty.model_copy(
        update={
            "poller": poller,
            "contracts": contracts,
            "rows": tuple(_row(record) for record in records) if product_id else (),
        }
    )
    return _report(generated_at, payload, _components(poller, contracts))


def _judged_until(state: FuturesPollState | None) -> datetime | None:
    """Hours before the last successful poll's hour are the latest gaps can be judged."""
    if state is None or state.last_success_at is None:
        return None
    return state.last_success_at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _contracts(
    records: tuple[FundingRateRecord, ...],
    starts: dict[str, datetime],
    around_the_clock: dict[str, bool],
    product_id: str | None,
    window_starts_at: datetime,
    judged_until: datetime | None,
) -> tuple[FundingContractSummary, ...]:
    """Summarize each contract with stored history (or the one requested)."""
    by_product: dict[str, list[FundingRateRecord]] = {}
    for record in records:
        by_product.setdefault(record.product_id, []).append(record)
    product_ids = sorted(set(starts) if product_id is None else {product_id})
    summaries: list[FundingContractSummary] = []
    for pid in product_ids:
        rows = by_product.get(pid, [])
        latest = rows[-1] if rows else None
        interval = timedelta(seconds=latest.interval_seconds) if latest else _HOUR
        gaps = (
            funding_gaps(
                rows,
                history_starts_at=starts.get(pid),
                starts_at=window_starts_at,
                ends_at=judged_until,
                interval=interval,
            )
            if judged_until is not None
            else ()
        )
        summaries.append(
            FundingContractSummary(
                product_id=pid,
                history_starts_at=starts.get(pid),
                twenty_four_by_seven=around_the_clock.get(pid),
                latest_funding_time=None if latest is None else latest.funding_time,
                latest_rate=None if latest is None else str(latest.rate),
                latest_settled=None if latest is None else latest.settled,
                stored_hours=len(rows),
                settled_hours=sum(row.settled for row in rows),
                gap_hours=len(gaps),
                gap_times=gaps[:_MAX_GAP_TIMES],
                revision_count=sum(row.revision_count for row in rows),
                conflict_count=sum(row.conflict_count for row in rows),
            )
        )
    return tuple(summaries)


def _poller(state: FuturesPollState, now: datetime) -> FundingPollerSummary:
    """Project the stored poll state, judging staleness from the last success."""
    stale = state.last_success_at is None or now - state.last_success_at > _STALE_AFTER
    return FundingPollerSummary(
        last_attempt_at=state.last_attempt_at,
        last_success_at=state.last_success_at,
        consecutive_failures=state.consecutive_failures,
        failure_code=state.failure_code,
        contract_count=state.contract_count,
        perpetual_count=state.perpetual_count,
        stale=stale,
    )


def _row(record: FundingRateRecord) -> FundingRateRow:
    """Project one stored hour."""
    return FundingRateRow(
        funding_time=record.funding_time,
        rate=str(record.rate),
        interval_seconds=record.interval_seconds,
        settled=record.settled,
        observation_count=record.observation_count,
        revision_count=record.revision_count,
        conflict_count=record.conflict_count,
        last_conflict_rate=(
            None if record.last_conflict_rate is None else str(record.last_conflict_rate)
        ),
        first_observed_at=record.first_observed_at,
        last_observed_at=record.last_observed_at,
    )


def _components(
    poller: FundingPollerSummary | None, contracts: tuple[FundingContractSummary, ...]
) -> tuple[ComponentReport, ...]:
    """Poller and history components with stable reason codes."""
    if poller is None:
        poll = _component(ReportStatus.DEGRADED, "FUTURES_POLLER_NOT_RUN", name="futures_poller")
    elif poller.stale:
        poll = _component(ReportStatus.DEGRADED, "FUTURES_POLLER_STALE", name="futures_poller")
    else:
        poll = _component(ReportStatus.HEALTHY, "OK", name="futures_poller")
    # Session-limited contracts have no funding hours while closed; only 24/7 gaps count.
    gaps = sum(c.gap_hours for c in contracts if c.twenty_four_by_seven is not False)
    conflicts = sum(contract.conflict_count for contract in contracts)
    if conflicts:
        history = _component(ReportStatus.DEGRADED, "FUNDING_RATE_CONFLICT")
    elif gaps:
        history = _component(ReportStatus.DEGRADED, "FUNDING_HISTORY_GAPS")
    else:
        history = _component(ReportStatus.HEALTHY, "OK")
    return (poll, history)


_DETAILS: dict[str, str] = {
    "OK": "Funding history is recorded with no gaps or conflicts in the window.",
    "STORE_DISABLED": "Futures observation storage is not configured (no database).",
    "STORE_UNAVAILABLE": "Futures observation storage could not be read.",
    "FUTURES_POLLER_NOT_RUN": (
        "The market-data worker has not polled the futures listing yet; it needs Coinbase "
        "credentials and a running market-data worker."
    ),
    "FUTURES_POLLER_STALE": (
        "The futures poller has not succeeded in the last three intervals; check the "
        "market-data worker and failure_code."
    ),
    "FUNDING_HISTORY_GAPS": "Some funding hours were never observed; they stay missing.",
    "FUNDING_RATE_CONFLICT": (
        "A later listing disagreed with a settled rate; the settled rate was kept."
    ),
}


def _component(
    status: ReportStatus, reason_code: str, *, name: str = "funding_history"
) -> ComponentReport:
    """One component with its fixed detail text."""
    return ComponentReport(
        name=name, status=status, reason_code=reason_code, detail=_DETAILS[reason_code]
    )


def _report(
    generated_at: datetime,
    payload: FundingPayload,
    components: tuple[ComponentReport, ...],
) -> FundingReport:
    """Wrap a payload in the standard envelope."""
    return FundingReport(
        application_version=__version__,
        generated_at=generated_at,
        overall_status=aggregate_status(components),
        components=components,
        redaction=STANDARD_REDACTION,
        recommended_next_action=recommend_next_action(components),
        payload=payload,
    )
