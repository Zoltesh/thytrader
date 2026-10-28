"""Desk wake alerts: a failing harness or a stopped timer is visible (ADR 0147, 0115)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from thytrader.agent_desk.models import (
    Desk,
    DeskAction,
    DeskStatus,
    DeskWake,
    WakeOutcome,
)
from thytrader.alerts.models import (
    AlertCheck,
    AlertCode,
    AlertScope,
    AlertSeverity,
    SafetyEvidence,
    new_alert,
)
from thytrader.alerts.supervision_desks import (
    FAILED_WAKES,
    STALL_GRACE,
    desk_subject,
    desk_wake_evidence,
)

_NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def _desk(mode: str = "paper", status: DeskStatus = DeskStatus.ACTIVE, **changes: object) -> Desk:
    """A 30-minute desk created a day ago."""
    values: dict[str, object] = {
        "desk_id": "desk-1",
        "name": "test desk",
        "mode": mode,
        "quote_currency": "USDC",
        "starting_cash": Decimal(1000),
        "cash": Decimal(1000),
        "products": ("BTC-USDC",),
        "status": status,
        "created_at": _NOW - timedelta(days=1),
        "updated_at": _NOW - timedelta(days=1),
    }
    return Desk.model_validate({**values, **changes})


def _wake(outcome: WakeOutcome, minutes_ago: int) -> DeskWake:
    """One wake ``minutes_ago`` before now."""
    at = _NOW - timedelta(minutes=minutes_ago)
    action = {
        WakeOutcome.APPLIED: DeskAction.NO_TRADE,
        WakeOutcome.SKIPPED: DeskAction.SKIPPED,
    }.get(outcome, DeskAction.INVALID)
    return DeskWake(
        wake_id=f"w{minutes_ago}",
        desk_id="desk-1",
        started_at=at,
        finished_at=at,
        harness="pi",
        briefing_fingerprint="sha256:" + "a" * 64,
        raw_response="",
        action=action,
        outcome=outcome,
        outcome_code="DESK_HARNESS_FAILED" if outcome is WakeOutcome.ERROR else None,
        detail="DESK_HARNESS_EXIT: exit 1: 403 out of credits"
        if outcome is WakeOutcome.ERROR
        else "",
    )


def _codes(evidence: SafetyEvidence) -> tuple[list[AlertCode], list[AlertCode]]:
    """The finding codes and the evaluated codes."""
    findings = [finding.code for finding in evidence.findings]
    evaluated = [check.code for check in evidence.evaluated]
    return findings, evaluated


@pytest.mark.parametrize(("mode", "severity"), [("live", "critical"), ("paper", "warning")])
def test_consecutive_failed_wakes_raise_a_failing_alert(mode: str, severity: str) -> None:
    """Three failed wakes in a row page: critical for live, a warning for paper."""
    wakes = [_wake(WakeOutcome.ERROR, 30 * index) for index in range(FAILED_WAKES)]
    desk = _desk(mode, live_acknowledged_at=_NOW if mode == "live" else None)
    evidence = desk_wake_evidence([desk], {"desk-1": wakes}, now=_NOW)
    findings, evaluated = _codes(evidence)
    assert findings == [AlertCode.DESK_WAKES_FAILING]
    assert evaluated == [AlertCode.DESK_WAKES_STALLED]
    finding = evidence.findings[0]
    assert finding.scope is AlertScope.DESK
    assert finding.subject == desk_subject("desk-1")
    assert finding.severity is AlertSeverity(severity)
    assert "DESK_HARNESS_FAILED, DESK_HARNESS_EXIT" in finding.detail
    assert "403" not in finding.detail
    assert "credits)" not in finding.detail


def test_fewer_failures_or_an_answered_wake_in_between_resolve() -> None:
    """Two failures, or a skip or answer as the newest wake, prove the desk is answering."""
    two = [
        _wake(WakeOutcome.ERROR, 0),
        _wake(WakeOutcome.INVALID, 30),
        _wake(WakeOutcome.APPLIED, 60),
    ]
    recovered = [
        _wake(WakeOutcome.APPLIED, 0),
        _wake(WakeOutcome.ERROR, 30),
        _wake(WakeOutcome.ERROR, 60),
    ]
    for wakes in (two, recovered):
        findings, evaluated = _codes(desk_wake_evidence([_desk()], {"desk-1": wakes}, now=_NOW))
        assert findings == []
        assert set(evaluated) == {AlertCode.DESK_WAKES_FAILING, AlertCode.DESK_WAKES_STALLED}


def test_a_desk_that_stopped_waking_raises_a_stalled_alert() -> None:
    """No wake for three cadences plus the grace; a skipped wake counts as a wake."""
    window = 3 * 30 + STALL_GRACE.total_seconds() / 60
    late = [_wake(WakeOutcome.SKIPPED, int(window) + 1)]
    on_time = [_wake(WakeOutcome.SKIPPED, int(window) - 1)]
    stalled, _ = _codes(desk_wake_evidence([_desk()], {"desk-1": late}, now=_NOW))
    fine, evaluated = _codes(desk_wake_evidence([_desk()], {"desk-1": on_time}, now=_NOW))
    assert stalled == [AlertCode.DESK_WAKES_STALLED]
    assert fine == []
    assert AlertCode.DESK_WAKES_STALLED in evaluated


def test_a_desk_never_woken_counts_from_its_creation_or_when_it_became_due() -> None:
    """A brand-new or just-resumed desk gets its full window before it is stalled."""
    fresh = _desk(created_at=_NOW - timedelta(minutes=20), updated_at=_NOW - timedelta(minutes=20))
    resumed = _desk(due_since=_NOW - timedelta(minutes=5))
    old = [_wake(WakeOutcome.APPLIED, 600)]
    assert _codes(desk_wake_evidence([fresh], {"desk-1": []}, now=_NOW))[0] == []
    assert _codes(desk_wake_evidence([resumed], {"desk-1": old}, now=_NOW))[0] == []
    assert _codes(desk_wake_evidence([_desk()], {"desk-1": []}, now=_NOW))[0] == [
        AlertCode.DESK_WAKES_STALLED
    ]


def test_a_paused_or_retired_desk_is_not_expected_to_wake() -> None:
    """Its alerts resolve whatever its wakes say."""
    failing = [_wake(WakeOutcome.ERROR, 600 + index) for index in range(FAILED_WAKES)]
    for status in (DeskStatus.PAUSED, DeskStatus.RETIRED):
        findings, evaluated = _codes(
            desk_wake_evidence([_desk(status=status)], {"desk-1": failing}, now=_NOW)
        )
        assert findings == []
        assert set(evaluated) == {AlertCode.DESK_WAKES_FAILING, AlertCode.DESK_WAKES_STALLED}


def test_an_unread_desk_proves_nothing_and_a_removed_desk_resolves() -> None:
    """A failed wake read neither raises nor resolves; a desk no longer listed resolves."""
    prior = new_alert(
        desk_wake_evidence(
            [_desk(desk_id="gone")],
            {"gone": [_wake(WakeOutcome.ERROR, index) for index in range(FAILED_WAKES)]},
            now=_NOW,
        ).findings[0],
        now=_NOW,
    )
    evidence = desk_wake_evidence([_desk()], {"desk-1": None}, now=_NOW, prior_alerts=[prior])
    assert evidence.findings == ()
    assert evidence.evaluated == (AlertCheck(AlertCode.DESK_WAKES_FAILING, "desk:gone"),)


def test_bookkeeping_writes_never_hide_a_stopped_timer() -> None:
    """A settlement or day roll moves updated_at but is not a wake; the desk is still stalled."""
    touched = _desk(updated_at=_NOW - timedelta(minutes=1))
    old = [_wake(WakeOutcome.APPLIED, 600)]
    assert _codes(desk_wake_evidence([touched], {"desk-1": old}, now=_NOW))[0] == [
        AlertCode.DESK_WAKES_STALLED
    ]


def test_recency_is_the_journal_time_not_a_callers_start_time() -> None:
    """A wake that claims a far-future start is judged by when ThyTrader journaled it."""
    future = _wake(WakeOutcome.APPLIED, 600).model_copy(
        update={"started_at": _NOW + timedelta(days=1)}
    )
    assert _codes(desk_wake_evidence([_desk()], {"desk-1": [future]}, now=_NOW))[0] == [
        AlertCode.DESK_WAKES_STALLED
    ]


def test_one_unjudgeable_desk_never_stops_the_others() -> None:
    """A row that cannot be compared proves nothing for its desk; the other desk still pages."""
    naive = _wake(WakeOutcome.APPLIED, 0).model_copy(
        update={"finished_at": _NOW.replace(tzinfo=None)}
    )
    failing = [_wake(WakeOutcome.ERROR, index) for index in range(FAILED_WAKES)]
    evidence = desk_wake_evidence(
        [_desk(desk_id="broken"), _desk()],
        {"broken": [naive], "desk-1": failing},
        now=_NOW,
    )
    assert [finding.subject for finding in evidence.findings] == ["desk:desk-1"]
    assert AlertCheck(AlertCode.DESK_WAKES_STALLED, "desk:broken") not in evidence.evaluated


def test_an_unjudgeable_stall_check_keeps_the_failing_finding() -> None:
    """Failing wakes still page when the stall check cannot be judged for the same desk."""
    failing = [_wake(WakeOutcome.ERROR, index) for index in range(FAILED_WAKES)]
    failing[0] = failing[0].model_copy(update={"finished_at": _NOW.replace(tzinfo=None)})
    evidence = desk_wake_evidence([_desk()], {"desk-1": failing}, now=_NOW)
    assert [finding.code for finding in evidence.findings] == [AlertCode.DESK_WAKES_FAILING]
    assert all(check.code is not AlertCode.DESK_WAKES_STALLED for check in evidence.evaluated)


def test_only_known_failure_codes_reach_the_alert() -> None:
    """A runner's free text that merely looks like a code is never copied into an alert."""
    leaked = [
        _wake(WakeOutcome.ERROR, index).model_copy(
            update={"detail": "SYNTHETIC_CREDENTIAL_0123456789: authentication rejected"}
        )
        for index in range(FAILED_WAKES)
    ]
    evidence = desk_wake_evidence([_desk()], {"desk-1": leaked}, now=_NOW)
    detail = evidence.findings[0].detail
    assert "SYNTHETIC_CREDENTIAL" not in detail
    assert "DESK_HARNESS_FAILED" in detail


@pytest.mark.parametrize("code", ["DESK_CONFIG_CHANGED", "DESK_LIVE_IDENTITY_MISMATCH"])
def test_a_run_of_identity_refusals_raises_the_failing_alert(code: str) -> None:
    """A runner the desk no longer accepts is refused forever; that is a failing desk."""
    refused = [
        _wake(WakeOutcome.APPLIED, index).model_copy(
            update={"outcome": WakeOutcome.REFUSED, "outcome_code": code}
        )
        for index in range(FAILED_WAKES)
    ]
    other = [wake.model_copy(update={"outcome_code": "DESK_MAX_POSITIONS"}) for wake in refused]
    raised = desk_wake_evidence([_desk()], {"desk-1": refused}, now=_NOW)
    quiet = desk_wake_evidence([_desk()], {"desk-1": other}, now=_NOW)
    assert [finding.code for finding in raised.findings] == [AlertCode.DESK_WAKES_FAILING]
    assert code in raised.findings[0].detail
    assert quiet.findings == ()
