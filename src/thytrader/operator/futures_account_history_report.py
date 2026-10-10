"""Operator ``futures-account --history``: the CFM mirror snapshots over a window (ADR 0127).

Read-only. Built to supervise a manual futures trade: every row is one mirror cycle with
the full USD balance summary (both margin-window measures included), positions in
contracts with side and average entry, the margin window type, and the same cycle's spot
USDC and USD balances. Consecutive rows show how CFM draws on USDC collateral, the
commission, funding, initial margin against ``liquidation_threshold``, and the overnight
margin step-up. Gaps longer than three mirror cycles and margin-window changes are listed
so neither has to be found by eye. USD and USDC are never added together.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING, Literal

from thytrader import __version__
from thytrader.exchanges.futures_models import (
    SHARED_COLLATERAL_NOTE,
    FuturesAccountStoreUnavailableError,
)
from thytrader.operator.futures_account_report import (
    FuturesBalancePayload,
    FuturesPositionPayload,
    SpotCollateralPayload,
    balance_payload,
    margin_ratio_text,
    positions_payload,
    spot_collateral_payload,
)
from thytrader.operator.models import (
    PORTFOLIO_REDACTION,
    ComponentReport,
    OperatorEnvelope,
    ReportStatus,
    _FrozenModel,
)
from thytrader.operator.status import aggregate_status, recommend_next_action

if TYPE_CHECKING:
    from thytrader.exchanges.futures_models import (
        FuturesAccountHistoryStore,
        FuturesAccountObservation,
    )

FUTURES_HISTORY_MAX_ROWS = 2880
"""Most rows one report returns: 48 hours of 60-second cycles. Page with a later ``since``."""
FUTURES_MIRROR_INTERVAL_SECONDS = 60
# Three missed 60-second cycles count as a gap, matching the latest report's staleness.
_GAP_AFTER = timedelta(seconds=180)


class FuturesAccountHistoryRow(_FrozenModel):
    """One mirror cycle. ``null`` is unknown, never zero; ``positions: []`` is flat."""

    observed_at: datetime
    enablement: Literal["enabled", "not_enabled", "unknown"]
    read_failures: tuple[str, ...]
    balance: FuturesBalancePayload | None
    positions: tuple[FuturesPositionPayload, ...] | None
    intraday_margin_setting: str | None
    margin_window_type: str | None
    margin_window_end_at: datetime | None
    margin_ratio: str | None
    spot_collateral: SpotCollateralPayload | None


class FuturesMirrorGap(_FrozenModel):
    """A stretch longer than three mirror cycles with no snapshot (window edges included)."""

    start: datetime
    end: datetime
    seconds: int


class FuturesMarginWindowChange(_FrozenModel):
    """The first row whose ``margin_window_type`` differs from the previous row's."""

    observed_at: datetime
    previous_window_type: str | None
    window_type: str | None


class FuturesAccountHistoryPayload(_FrozenModel):
    """Mirror snapshots with ``since <= observed_at < until``, oldest first.

    ``truncated`` is true when more than ``max_rows`` rows matched: the oldest ``max_rows``
    are returned, and the next page starts at the last row's ``observed_at`` plus one
    second. ``spot_unknown_rows`` counts rows without spot balances (the spot read failed,
    or the row predates Alembic 0074); ``read_failure_rows`` counts rows with any failed read.
    """

    since: datetime
    until: datetime
    mirror_interval_seconds: int = FUTURES_MIRROR_INTERVAL_SECONDS
    max_rows: int = FUTURES_HISTORY_MAX_ROWS
    row_count: int
    truncated: bool
    rows: tuple[FuturesAccountHistoryRow, ...]
    gaps: tuple[FuturesMirrorGap, ...]
    margin_window_changes: tuple[FuturesMarginWindowChange, ...]
    read_failure_rows: int
    spot_unknown_rows: int
    collateral_note: str = SHARED_COLLATERAL_NOTE
    orderable: Literal[False] = False


class FuturesAccountHistoryReport(OperatorEnvelope):
    """Read-only CFM futures account mirror history."""

    report_kind: Literal["futures_account_history"] = "futures_account_history"
    payload: FuturesAccountHistoryPayload


_DETAILS: dict[str, str] = {
    "OK": "Every mirror cycle in the window was recorded and every read succeeded.",
    "STORE_DISABLED": "Futures account storage is not configured (no database).",
    "STORE_UNAVAILABLE": "Futures account storage could not be read.",
    "FUTURES_HISTORY_EMPTY": (
        "No futures account snapshot falls in the window; check since/until and that the "
        "worker runs with Coinbase credentials."
    ),
    "FUTURES_HISTORY_TRUNCATED": (
        "More snapshots matched than max_rows; the oldest are shown. Continue with a later since."
    ),
    "FUTURES_HISTORY_GAPS": (
        "The mirror missed more than three cycles at least once; see gaps. Values there are "
        "unobserved."
    ),
    "FUTURES_READ_FAILURES": (
        "Some mirror reads failed in the window (CFM or the spot account listing); see each "
        "row's read_failures."
    ),
    "FUTURES_SPOT_BALANCES_UNKNOWN": (
        "Some rows have no spot USDC/USD balances (the spot read failed or the row predates "
        "Alembic 0074); USDC collateral movement is unknown there."
    ),
}


async def build_futures_account_history_report(
    store: FuturesAccountHistoryStore | None,
    *,
    since: datetime,
    until: datetime | None = None,
    now: datetime | None = None,
    max_rows: int = FUTURES_HISTORY_MAX_ROWS,
) -> FuturesAccountHistoryReport:
    """Assemble the history report for ``since <= observed_at < until`` (default: now)."""
    generated_at = now or datetime.now(UTC)
    end = until or generated_at
    if store is None:
        disabled = _component(ReportStatus.DEGRADED, "STORE_DISABLED")
        return _report(generated_at, _empty(since, end), (disabled,))
    try:
        observations = await store.history(since=since, until=end, limit=max_rows + 1)
    except FuturesAccountStoreUnavailableError:
        unavailable = _component(ReportStatus.FAILED, "STORE_UNAVAILABLE")
        return _report(generated_at, _empty(since, end), (unavailable,))
    truncated = len(observations) > max_rows
    kept = observations[:max_rows]
    rows = tuple(_row(observation) for observation in kept)
    observed_end = min(end, generated_at)
    payload = FuturesAccountHistoryPayload(
        since=since,
        until=end,
        max_rows=max_rows,
        row_count=len(rows),
        truncated=truncated,
        rows=rows,
        gaps=_gaps(since, observed_end, rows, truncated=truncated),
        margin_window_changes=_window_changes(rows),
        read_failure_rows=sum(1 for row in rows if row.read_failures),
        spot_unknown_rows=sum(1 for row in rows if row.spot_collateral is None),
    )
    return _report(generated_at, payload, _components(payload))


def _row(observation: FuturesAccountObservation) -> FuturesAccountHistoryRow:
    """Project one stored observation."""
    window = observation.margin_window
    return FuturesAccountHistoryRow(
        observed_at=observation.observed_at,
        enablement=observation.enablement.value,
        read_failures=observation.read_failures,
        balance=None if observation.balance is None else balance_payload(observation.balance),
        positions=positions_payload(observation.positions),
        intraday_margin_setting=observation.intraday_margin_setting,
        margin_window_type=None if window is None else window.margin_window_type,
        margin_window_end_at=None if window is None else window.end_time,
        margin_ratio=margin_ratio_text(observation),
        spot_collateral=spot_collateral_payload(observation.spot_balances),
    )


def _gaps(
    since: datetime,
    end: datetime,
    rows: tuple[FuturesAccountHistoryRow, ...],
    *,
    truncated: bool,
) -> tuple[FuturesMirrorGap, ...]:
    """Unobserved stretches longer than three cycles, including the window's edges.

    A truncated page has no trailing gap: the rows after it exist on the next page.
    """
    instants = [since, *(row.observed_at for row in rows)]
    if not truncated and end > since:
        instants.append(end)
    gaps: list[FuturesMirrorGap] = []
    for start, stop in pairwise(instants):
        if stop - start > _GAP_AFTER:
            seconds = int((stop - start).total_seconds())
            gaps.append(FuturesMirrorGap(start=start, end=stop, seconds=seconds))
    return tuple(gaps)


def _window_changes(
    rows: tuple[FuturesAccountHistoryRow, ...],
) -> tuple[FuturesMarginWindowChange, ...]:
    """Rows where the margin window type changed (the 16:00 ET overnight step-up, etc.)."""
    changes: list[FuturesMarginWindowChange] = []
    for previous, current in pairwise(rows):
        if current.margin_window_type != previous.margin_window_type:
            changes.append(
                FuturesMarginWindowChange(
                    observed_at=current.observed_at,
                    previous_window_type=previous.margin_window_type,
                    window_type=current.margin_window_type,
                )
            )
    return tuple(changes)


def _components(payload: FuturesAccountHistoryPayload) -> tuple[ComponentReport, ...]:
    """Empty, truncated, gaps, read failures, then unknown spot balances."""
    if not payload.rows:
        return (_component(ReportStatus.DEGRADED, "FUTURES_HISTORY_EMPTY"),)
    codes = [
        code
        for code, present in (
            ("FUTURES_HISTORY_TRUNCATED", payload.truncated),
            ("FUTURES_HISTORY_GAPS", bool(payload.gaps)),
            ("FUTURES_READ_FAILURES", payload.read_failure_rows > 0),
            ("FUTURES_SPOT_BALANCES_UNKNOWN", payload.spot_unknown_rows > 0),
        )
        if present
    ]
    components = tuple(_component(ReportStatus.DEGRADED, code) for code in codes)
    return components or (_component(ReportStatus.HEALTHY, "OK"),)


def _empty(since: datetime, until: datetime) -> FuturesAccountHistoryPayload:
    """The payload when storage cannot be read: no rows, nothing inferred."""
    return FuturesAccountHistoryPayload(
        since=since,
        until=until,
        row_count=0,
        truncated=False,
        rows=(),
        gaps=(),
        margin_window_changes=(),
        read_failure_rows=0,
        spot_unknown_rows=0,
    )


def _component(status: ReportStatus, reason_code: str) -> ComponentReport:
    """One component with its fixed detail."""
    return ComponentReport(
        name="futures_account_history",
        status=status,
        reason_code=reason_code,
        detail=_DETAILS[reason_code],
    )


def _report(
    generated_at: datetime,
    payload: FuturesAccountHistoryPayload,
    components: tuple[ComponentReport, ...],
) -> FuturesAccountHistoryReport:
    """Wrap the payload; balances are shown, identifiers and secrets never are."""
    return FuturesAccountHistoryReport(
        application_version=__version__,
        generated_at=generated_at,
        overall_status=aggregate_status(components),
        components=components,
        redaction=PORTFOLIO_REDACTION,
        recommended_next_action=recommend_next_action(components),
        payload=payload,
    )
