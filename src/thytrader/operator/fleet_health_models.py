"""Payload models of the operator ``fleet-health`` report and its readiness/risk sections.

``FleetEntriesPayload`` is the fleet entry readiness evaluation (ADR 0130): per mode and
quote scope, whether the entry gate admits any new entry (``entries_admissible``), the
blocking reason codes and the exact blocking books. ``FleetDecisionLogPayload`` aggregates
the per-bar decision journal of running books over a recent window and flags systemic
blockers. The ``readiness`` and ``risk`` reports embed ``FleetEntriesPayload`` as
``fleet_entries``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from thytrader.operator.models import OperatorEnvelope, _FrozenModel

_CODE = r"^[A-Z][A-Z0-9_]{0,63}$"

FleetCheck = Literal[
    "fleet_disarm",
    "accounting_inventory",
    "quote_scope",
    "venue_quote_balance",
    "futures_collateral",
    "open_position_slots",
    "exposure_cap",
    "btc_beta",
    "unresolved_accounting",
    "daily_loss",
    "drawdown_latch",
    "linked_futures_breaker",
    "entry_cluster",
]
Admissibility = Literal["yes", "blocked", "unknown"]


class FleetBlockingBookPayload(_FrozenModel):
    """One book that makes a check block, and the exact record or rule responsible."""

    deployment_id: UUID
    status: Literal["running", "paused", "stopped"]
    product_id: str = Field(min_length=1, max_length=64)
    detail: str = Field(max_length=1000)


class FleetEntryCheckPayload(_FrozenModel):
    """One gate check evaluated for a whole scope.

    ``fleet_wide`` blocks every entry in the scope; otherwise only ``deployments`` are
    stopped (for example one strategy's drawdown latch). ``blocker_class`` says what clears
    it: ``evidence`` a repair, ``latch`` an operator reset, ``policy`` an operator decision
    (manual futures on the shared collateral), ``capacity`` an exit (slots or exposure caps
    full), ``transient`` time (the clustering window), ``operator`` a fleet rearm. Only the
    first three are alerted.
    """

    check: FleetCheck
    status: Literal["pass", "blocked", "unknown", "not_applicable"]
    reason_code: str | None = Field(default=None, pattern=_CODE)
    blocker_class: (
        Literal["evidence", "latch", "policy", "capacity", "transient", "operator"] | None
    ) = None
    fleet_wide: bool = False
    detail: str = Field(default="", max_length=1000)
    deployments: tuple[FleetBlockingBookPayload, ...] = ()


class FleetEntryScopePayload(_FrozenModel):
    """Whether any new entry is admissible in one mode and quote/settlement scope."""

    mode: Literal["paper", "live"]
    scope: str = Field(min_length=1, max_length=16, description="USD, USDC, USDT or CFM-USD.")
    entries_admissible: Admissibility
    reason_codes: tuple[str, ...] = ()
    blocking_deployment_ids: tuple[UUID, ...] = ()
    running_deployments: int = Field(ge=0)
    occupied_deployments: int = Field(ge=0)
    alert_subject: str = Field(min_length=1, max_length=128)
    checks: tuple[FleetEntryCheckPayload, ...]


class FleetEntriesPayload(_FrozenModel):
    """Fleet entry readiness across every occupied scope (ADR 0130).

    ``live_entries_admissible`` is the worst live scope (``yes`` when no live book is
    occupied). ``complete`` is false only when the fleet could not be listed; then
    ``scopes`` is empty and the state is unknown, never healthy.
    """

    evaluated_at: datetime
    complete: bool
    detail: str = Field(default="", max_length=500)
    live_entries_admissible: Admissibility
    paper_entries_admissible: Admissibility
    scopes: tuple[FleetEntryScopePayload, ...] = ()


class FleetDecisionReasonPayload(_FrozenModel):
    """One ``entry_blocked`` or ``skipped`` reason across running books in the window."""

    outcome: Literal["entry_blocked", "skipped"]
    reason_code: str = Field(pattern=_CODE)
    skip_reason: str | None = Field(default=None, max_length=32)
    rows: int = Field(ge=1)
    deployments: int = Field(ge=1)
    deployment_ids: tuple[UUID, ...] = Field(default=(), max_length=20)
    latest_bar_closes_at: datetime


class FleetSystemicBlockerPayload(_FrozenModel):
    """A decision-log pattern that points at a fleet problem rather than one bot.

    ``evidence_block``: an evidence-type denial (missing marks or accounting, unknown
    collateral, venue balance or beta). ``shared_reason``: the same reason on two or more
    bots. ``sizing_skips``: matched signals repeatedly skipped by sizing. ``warmup_stuck``:
    bots still warming up after many bars.
    """

    kind: Literal["evidence_block", "shared_reason", "sizing_skips", "warmup_stuck"]
    outcome: Literal["entry_blocked", "skipped"]
    reason_code: str = Field(pattern=_CODE)
    rows: int = Field(ge=1)
    deployments: int = Field(ge=1)
    deployment_ids: tuple[UUID, ...] = Field(default=(), max_length=20)
    detail: str = Field(max_length=500)


class FleetDecisionLogPayload(_FrozenModel):
    """``entry_blocked`` and ``skipped`` reasons of running books over a trailing window."""

    storage: Literal["available", "unavailable"]
    window_hours: int = Field(ge=1)
    since: datetime
    running_deployments: int = Field(ge=0)
    rows_read: int = Field(ge=0)
    truncated: bool = False
    unreadable_deployment_ids: tuple[UUID, ...] = ()
    reasons: tuple[FleetDecisionReasonPayload, ...] = ()
    systemic: tuple[FleetSystemicBlockerPayload, ...] = ()


class FleetAlertPayload(_FrozenModel):
    """One open ``FLEET_ENTRIES_BLOCKED`` alert from the durable feed."""

    subject: str = Field(min_length=1, max_length=128)
    severity: Literal["info", "warning", "critical"]
    detail: str = Field(max_length=500)
    first_seen_at: datetime
    last_seen_at: datetime
    occurrences: int = Field(ge=1)


class FleetHealthPayload(_FrozenModel):
    """Fleet entry readiness, the decision-log aggregation and the open fleet alerts."""

    entries: FleetEntriesPayload
    decisions: FleetDecisionLogPayload
    alert_storage: Literal["available", "unavailable"]
    open_fleet_alerts: tuple[FleetAlertPayload, ...] = ()


class FleetHealthReport(OperatorEnvelope):
    """Read-only fleet entry health. This report cannot place or cancel orders."""

    report_kind: Literal["fleet_health"] = "fleet_health"
    payload: FleetHealthPayload
