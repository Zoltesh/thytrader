"""An open alert that escalates in place after its page is paged again, once (ADR 0115)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4, uuid5

import pytest

from tests.alerts.test_alert_service import _BlockingSender, _entry_block, _FailureSender
from thytrader.alerts.models import (
    AlertCode,
    AlertScope,
    AlertSeverity,
    SupervisionFinding,
    new_alert,
)
from thytrader.alerts.service import AlertService, page_id
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.alerts.supervision_inputs import AlertThresholds
from thytrader.config import NotifyProvider
from thytrader.memory.notify import RecordingNotificationSender

if TYPE_CHECKING:
    from thytrader.alerts.models import OperatorAlert
    from thytrader.alerts.service import AlertDeliverySender
    from thytrader.alerts.store import AlertStore

_NOW = datetime(2026, 3, 2, 12, tzinfo=UTC)
pytestmark = pytest.mark.anyio


def _uncovered(severity: AlertSeverity = AlertSeverity.WARNING) -> SupervisionFinding:
    """One book-scoped identity that can change severity while it stays open."""
    return SupervisionFinding(
        code=AlertCode.STOP_COVERAGE_UNKNOWN,
        scope=AlertScope.DEPLOYMENT,
        subject="00000000-0000-7000-8000-000000000001",
        severity=severity,
        detail="protective cover could not be verified",
    )


def _service(
    store: AlertStore, sender: AlertDeliverySender, *, attempts: int = 5, minimum: str = "info"
) -> AlertService:
    return AlertService(
        store,
        sender,
        thresholds=AlertThresholds(delivery_max_attempts=attempts),
        min_severity=lambda: minimum,
    )


async def _open(store: AlertStore) -> OperatorAlert:
    (row,) = await store.list_open_alerts()
    return row


async def assert_escalation_in_place_is_paged_once(store: AlertStore) -> None:
    """A warning page, then critical in place: one more page with its own stable id."""
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = _service(store, sender)
    await service.apply((_uncovered(),), now=_NOW)
    first = await _open(store)
    assert first.covered_severity is AlertSeverity.WARNING
    await service.apply((_uncovered(AlertSeverity.CRITICAL),), now=_NOW + timedelta(minutes=1))
    for minute in range(2, 6):
        await service.apply(
            (_uncovered(AlertSeverity.CRITICAL),), now=_NOW + timedelta(minutes=minute)
        )
    assert [record.title for record in sender.delivered] == [
        "[thytrader] WARNING: STOP_COVERAGE_UNKNOWN",
        "[thytrader] CRITICAL: STOP_COVERAGE_UNKNOWN",
    ]
    assert sender.delivered[0].id == first.id
    assert sender.delivered[1].id == uuid5(first.id, "severity:critical")
    assert "escalated from warning" in sender.delivered[1].body
    row = await _open(store)
    assert row.id == first.id
    assert row.covered_severity is AlertSeverity.CRITICAL
    assert row.delivery_status == "delivered"


async def test_an_escalation_in_place_is_paged_once_with_its_own_id() -> None:
    """Receivers that dedupe on the notification id keep the escalation page."""
    await assert_escalation_in_place_is_paged_once(InMemoryAlertStore())


async def test_a_downgrade_in_place_is_never_paged() -> None:
    """A critical page covers the same row when it falls back to warning and rises again."""
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = _service(store, sender)
    await service.apply((_uncovered(AlertSeverity.CRITICAL),), now=_NOW)
    await service.apply((_uncovered(),), now=_NOW + timedelta(minutes=1))
    await service.apply((_uncovered(AlertSeverity.CRITICAL),), now=_NOW + timedelta(minutes=2))
    assert len(sender.delivered) == 1


async def test_a_warning_kept_local_by_the_minimum_pages_once_at_its_first_id() -> None:
    """A row never sent is a first page when it turns critical, not an escalation page."""
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = _service(store, sender, minimum="critical")
    await service.apply((_uncovered(),), now=_NOW)
    assert sender.delivered == []
    await service.apply((_uncovered(AlertSeverity.CRITICAL),), now=_NOW + timedelta(minutes=1))
    row = await _open(store)
    assert [record.id for record in sender.delivered] == [row.id]
    assert "escalated" not in sender.delivered[0].body


async def test_a_row_sent_before_coverage_was_recorded_is_not_re_paged_in_place() -> None:
    """Without known coverage there is nothing to compare: an upgrade pages no old row."""
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = _service(store, sender)
    await service.apply((_uncovered(),), now=_NOW, dispatch=False)
    identity = (AlertCode.STOP_COVERAGE_UNKNOWN.value, _uncovered().subject)
    store.history[identity][-1] = replace(store.history[identity][-1], delivery_status="delivered")
    await service.apply((_uncovered(AlertSeverity.CRITICAL),), now=_NOW + timedelta(minutes=1))
    assert sender.delivered == []


async def test_an_escalation_observed_while_the_first_page_is_in_flight_still_pages() -> None:
    """The in-flight warning send acknowledges warning coverage; the next dispatch re-pages."""
    store = InMemoryAlertStore()
    sender = _BlockingSender()
    service = _service(store, sender)
    first = asyncio.create_task(service.apply((_uncovered(),), now=_NOW))
    await sender.started.wait()
    await service.apply(
        (_uncovered(AlertSeverity.CRITICAL),), now=_NOW + timedelta(seconds=1), dispatch=False
    )
    sender.release.set()
    await first
    assert (await _open(store)).covered_severity is AlertSeverity.WARNING
    await service.dispatch_pending(now=_NOW + timedelta(seconds=2))
    assert [record.severity.value for record in sender.delivered] == ["warning", "error"]


async def test_concurrent_dispatchers_re_page_an_escalation_once() -> None:
    """The compare-and-set re-arm and the delivery claim admit one escalation send."""
    store = InMemoryAlertStore()
    recorder = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    await _service(store, recorder).apply((_uncovered(),), now=_NOW)
    await _service(store, recorder).apply(
        (_uncovered(AlertSeverity.CRITICAL),), now=_NOW + timedelta(seconds=1), dispatch=False
    )
    sender = _BlockingSender()
    one = asyncio.create_task(
        _service(store, sender).dispatch_pending(now=_NOW + timedelta(seconds=2))
    )
    await sender.started.wait()
    await _service(store, sender).dispatch_pending(now=_NOW + timedelta(seconds=3))
    sender.release.set()
    await one
    await _service(store, sender).dispatch_pending(now=_NOW + timedelta(seconds=4))
    assert len(sender.delivered) == 1


async def test_a_failing_escalation_page_keeps_one_bounded_budget() -> None:
    """Re-arming happens only from a sent row, so failed escalation sends never loop."""
    store = InMemoryAlertStore()
    await _service(store, RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)).apply(
        (_uncovered(),), now=_NOW
    )
    failing = _FailureSender()
    service = _service(store, failing, attempts=2)
    for second in range(1, 12):
        await service.apply(
            (_uncovered(AlertSeverity.CRITICAL),), now=_NOW + timedelta(seconds=second * 61)
        )
    assert failing.calls == 2
    row = await _open(store)
    assert row.delivery_status == "failed"
    assert row.covered_severity is AlertSeverity.WARNING


async def test_an_entry_block_escalated_in_place_after_its_page_is_paged_at_once() -> None:
    """It no longer waits to clear and return: the open row is re-paged at critical."""
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = _service(store, sender)
    critical = _entry_block()
    warning = replace(critical, severity=AlertSeverity.WARNING)
    for minute in range(7):
        await service.apply((warning,), now=_NOW + timedelta(minutes=minute))
    assert len(sender.delivered) == 1
    await service.apply((critical,), now=_NOW + timedelta(minutes=7))
    assert [record.title for record in sender.delivered] == [
        "[thytrader] WARNING: FLEET_ENTRIES_BLOCKED",
        "[thytrader] CRITICAL: FLEET_ENTRIES_BLOCKED",
    ]


def test_a_first_page_uses_the_alert_id_and_a_re_page_a_derived_one() -> None:
    """Every retry of one page keeps its id; pages at different severities never share one."""
    alert_id = uuid4()
    row = replace(new_alert(_uncovered(AlertSeverity.CRITICAL), now=_NOW), id=alert_id)
    assert page_id(row) == alert_id
    covered = replace(row, covered_severity=AlertSeverity.WARNING)
    assert page_id(covered) == uuid5(alert_id, "severity:critical")
    assert page_id(replace(covered, severity=AlertSeverity.WARNING)) != page_id(covered)
