"""A page per alert and severity: escalations page once, downgrades never (ADR 0115).

The shared ``assert_*`` scenarios run against both stores (see ``test_postgres_alerts``).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4, uuid5

import pytest

from tests.alerts.test_alert_service import _BlockingSender, _entry_block, _FailureSender
from thytrader.alerts.models import (
    AlertCheck,
    AlertCode,
    AlertScope,
    AlertSeverity,
    SupervisionFinding,
    new_alert,
)
from thytrader.alerts.service import AlertService, page_id
from thytrader.alerts.store import InMemoryAlertStore, page_attempts, page_claimable
from thytrader.alerts.supervision_inputs import AlertThresholds
from thytrader.config import NotifyProvider
from thytrader.memory.models import DeliveryStatus
from thytrader.memory.notify import DeliveryResult, RecordingNotificationSender
from thytrader.operator.alerts_report import _item

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from thytrader.alerts.models import OperatorAlert
    from thytrader.alerts.service import AlertDeliverySender
    from thytrader.alerts.store import AlertStore
    from thytrader.memory.models import NotificationRecord

_NOW = datetime(2026, 3, 2, 12, tzinfo=UTC)
_WARNING, _CRITICAL = AlertSeverity.WARNING, AlertSeverity.CRITICAL
pytestmark = pytest.mark.anyio


def _uncovered(severity: AlertSeverity = _WARNING) -> SupervisionFinding:
    """One book-scoped identity that can change severity while it stays open."""
    return SupervisionFinding(
        code=AlertCode.STOP_COVERAGE_UNKNOWN,
        scope=AlertScope.DEPLOYMENT,
        subject=str(uuid4()),
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


async def _row(store: AlertStore, subject: str) -> OperatorAlert:
    (row,) = [row for row in await store.list_open_alerts() if row.subject == subject]
    return row


class _Switch(RecordingNotificationSender):
    """A webhook that fails while ``failing`` is set and records every attempt."""

    def __init__(self) -> None:
        super().__init__(provider=NotifyProvider.WEBHOOK)
        self.failing = False
        self.attempts: list[NotificationRecord] = []

    async def deliver(self, record: NotificationRecord) -> DeliveryResult:
        self.attempts.append(record)
        if self.failing:
            return DeliveryResult(status=DeliveryStatus.FAILED)
        return await super().deliver(record)


async def assert_escalation_in_place_is_paged_once(store: AlertStore) -> None:
    """A warning page, then critical in place: one more page, each with its own id."""
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = _service(store, sender)
    finding = _uncovered()
    await service.apply((finding,), now=_NOW)
    first = await _row(store, finding.subject)
    assert first.covered_severity is _WARNING
    for minute in range(1, 6):
        await service.apply(
            (replace(finding, severity=_CRITICAL),), now=_NOW + timedelta(minutes=minute)
        )
    assert [record.title for record in sender.delivered] == [
        "[thytrader] WARNING: STOP_COVERAGE_UNKNOWN",
        "[thytrader] CRITICAL: STOP_COVERAGE_UNKNOWN",
    ]
    assert [record.id for record in sender.delivered] == [
        uuid5(first.id, "severity:warning"),
        uuid5(first.id, "severity:critical"),
    ]
    assert f"alert={first.id}" in sender.delivered[1].body
    assert "escalated from warning" in sender.delivered[1].body
    row = await _row(store, finding.subject)
    assert row.covered_severity is _CRITICAL
    assert row.delivery_status == "delivered"


async def assert_a_failed_escalation_is_not_followed_by_a_duplicate_downgrade(
    store: AlertStore,
) -> None:
    """Warning sent, critical fails, back to warning: no second warning; critical keeps its id.

    The escalation's attempt budget also survives the flap instead of resetting each return.
    """
    sender = _Switch()
    service = _service(store, sender, attempts=2)
    finding = _uncovered()
    critical = replace(finding, severity=_CRITICAL)
    await service.apply((finding,), now=_NOW)
    sender.failing = True
    for step in range(1, 12):
        await service.apply((critical,), now=_NOW + timedelta(seconds=61 * step))
        await service.apply((finding,), now=_NOW + timedelta(seconds=61 * step + 30))
    row = await _row(store, finding.subject)
    assert [record.title.split(":")[0] for record in sender.attempts] == [
        "[thytrader] WARNING",
        "[thytrader] CRITICAL",
        "[thytrader] CRITICAL",
    ]
    assert {record.id for record in sender.attempts[1:]} == {uuid5(row.id, "severity:critical")}
    assert (row.warning_attempts, row.critical_attempts, row.delivery_attempts) == (1, 2, 3)
    assert row.covered_severity is _WARNING


async def assert_an_ambiguous_lower_page_never_swallows_the_escalation(store: AlertStore) -> None:
    """A warning send whose acknowledgement was lost; the critical page is a different page."""
    sender = _BlockingSender()
    finding = _uncovered()
    task = asyncio.create_task(_service(store, sender).apply((finding,), now=_NOW))
    await sender.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    retry = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    await _service(store, retry).apply(
        (replace(finding, severity=_CRITICAL),), now=_NOW + timedelta(seconds=61)
    )
    row = await _row(store, finding.subject)
    assert sender.delivered[0].id == uuid5(row.id, "severity:warning")
    assert [record.id for record in retry.delivered] == [uuid5(row.id, "severity:critical")]
    assert (row.warning_attempts, row.critical_attempts) == (1, 1)


async def assert_an_exhausted_lower_page_does_not_block_the_escalation(store: AlertStore) -> None:
    """Warning attempts spent against a dead webhook; the critical page gets its own budget."""
    sender = _Switch()
    sender.failing = True
    service = _service(store, sender, attempts=2)
    finding = _uncovered()
    for step in range(4):
        await service.apply((finding,), now=_NOW + timedelta(seconds=61 * step))
    assert len(sender.attempts) == 2
    sender.failing = False
    await service.apply((replace(finding, severity=_CRITICAL),), now=_NOW + timedelta(minutes=10))
    row = await _row(store, finding.subject)
    assert [record.severity.value for record in sender.delivered] == ["error"]
    assert row.covered_severity is _CRITICAL


async def assert_a_downgrade_flap_never_starves_the_escalation(store: AlertStore) -> None:
    """Warning attempts during a flap spend only the warning page's budget.

    With the webhook down the row goes warning, critical, then warning until warning is
    exhausted; once the webhook recovers and the row is critical again, its page still goes out.
    """
    sender = _Switch()
    sender.failing = True
    service = _service(store, sender, attempts=2)
    finding = _uncovered()
    critical = replace(finding, severity=_CRITICAL)
    observations = (finding, critical, finding, finding, finding)
    for step, observed in enumerate(observations):
        await service.apply((observed,), now=_NOW + timedelta(seconds=61 * step))
    sender.failing = False
    await service.apply((critical,), now=_NOW + timedelta(minutes=10))
    row = await _row(store, finding.subject)
    assert [record.severity.value for record in sender.delivered] == ["error"]
    assert (row.warning_attempts, row.critical_attempts) == (2, 2)


async def assert_a_row_sent_before_coverage_was_recorded_is_not_re_paged(
    store: AlertStore, mark_sent: Callable[[OperatorAlert], Awaitable[None]]
) -> None:
    """Without known coverage there is nothing to compare: an upgrade pages no old row."""
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = _service(store, sender)
    finding = _uncovered()
    await service.apply((finding,), now=_NOW, dispatch=False)
    await mark_sent(await _row(store, finding.subject))
    await service.apply((replace(finding, severity=_CRITICAL),), now=_NOW + timedelta(minutes=1))
    assert sender.delivered == []


async def test_an_escalation_in_place_is_paged_once_with_its_own_id() -> None:
    """Receivers that dedupe on the notification id keep the escalation page."""
    await assert_escalation_in_place_is_paged_once(InMemoryAlertStore())


async def test_a_failed_escalation_is_not_followed_by_a_duplicate_downgrade() -> None:
    """The coverage fence and the per-page budget hold across a flap."""
    await assert_a_failed_escalation_is_not_followed_by_a_duplicate_downgrade(InMemoryAlertStore())


async def test_an_ambiguous_lower_page_never_swallows_the_escalation() -> None:
    """A lost acknowledgement cannot make the critical page look like a duplicate."""
    await assert_an_ambiguous_lower_page_never_swallows_the_escalation(InMemoryAlertStore())


async def test_an_exhausted_lower_page_does_not_block_the_escalation() -> None:
    """A higher severity is a new page with its own attempt budget."""
    await assert_an_exhausted_lower_page_does_not_block_the_escalation(InMemoryAlertStore())


async def test_a_downgrade_flap_never_starves_the_escalation() -> None:
    """Each severity spends its own budget."""
    await assert_a_downgrade_flap_never_starves_the_escalation(InMemoryAlertStore())


async def test_a_failed_return_covered_by_its_episode_page_is_not_paged_again() -> None:
    """Row A pages warning; return B fails at critical, then falls to warning: no warning twice."""
    store = InMemoryAlertStore()
    sender = _Switch()
    service = _service(store, sender)
    critical = _entry_block()
    warning = replace(critical, severity=_WARNING)
    check = AlertCheck(critical.code, critical.subject)
    for minute in range(7):
        await service.apply((warning,), now=_NOW + timedelta(minutes=minute))
    await service.apply((), now=_NOW + timedelta(minutes=7), evaluated=(check,))
    sender.failing = True
    await service.apply((critical,), now=_NOW + timedelta(minutes=8))
    sender.failing = False
    for minute in range(9, 14):
        await service.apply((warning,), now=_NOW + timedelta(minutes=minute))
    assert [record.severity.value for record in sender.attempts] == ["warning", "error"]
    await service.apply((critical,), now=_NOW + timedelta(minutes=15))
    assert [record.severity.value for record in sender.delivered] == ["warning", "error"]


def test_an_unsent_row_from_before_per_severity_budgets_gets_one_fresh_budget() -> None:
    """Alembic 0082 leaves an unsent row's counters at zero: one retry budget, never a miss."""
    row = replace(
        new_alert(_uncovered(_CRITICAL), now=_NOW), delivery_status="failed", delivery_attempts=5
    )
    assert page_claimable(row)
    assert page_attempts(row) == 0


@pytest.mark.parametrize("status", ["failed", "exhausted"])
async def test_delivery_off_never_erases_failure_evidence(status: str) -> None:
    """Like ``skip_delivery``, delivery off leaves a failed or exhausted row as it is."""
    store = InMemoryAlertStore()
    finding = _uncovered()
    await _service(store, RecordingNotificationSender()).apply((finding,), now=_NOW, dispatch=False)
    row = await _row(store, finding.subject)
    store.history[(row.code.value, row.subject)][-1] = replace(row, delivery_status=status)
    await store.claim_delivery(
        row.id, provider="none", now=_NOW, max_attempts=5, ttl=timedelta(seconds=60)
    )
    assert (await _row(store, finding.subject)).delivery_status == status


async def test_a_row_sent_before_coverage_was_recorded_is_not_re_paged() -> None:
    """A delivered row with no ``covered_severity`` (before Alembic 0081) is left alone."""
    store = InMemoryAlertStore()

    async def mark_sent(row: OperatorAlert) -> None:
        store.history[(row.code.value, row.subject)][-1] = replace(row, delivery_status="delivered")

    await assert_a_row_sent_before_coverage_was_recorded_is_not_re_paged(store, mark_sent)


async def test_a_downgrade_in_place_is_never_paged() -> None:
    """A critical page covers the row when it falls back to warning and rises again."""
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = _service(store, sender)
    finding = _uncovered(_CRITICAL)
    await service.apply((finding,), now=_NOW)
    await service.apply((replace(finding, severity=_WARNING),), now=_NOW + timedelta(minutes=1))
    await service.apply((finding,), now=_NOW + timedelta(minutes=2))
    assert len(sender.delivered) == 1


async def test_a_warning_kept_local_by_the_minimum_pages_once_when_critical() -> None:
    """A row never sent is paged once at critical, without an escalation note."""
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = _service(store, sender, minimum="critical")
    finding = _uncovered()
    await service.apply((finding,), now=_NOW)
    assert sender.delivered == []
    await service.apply((replace(finding, severity=_CRITICAL),), now=_NOW + timedelta(minutes=1))
    row = await _row(store, finding.subject)
    assert [record.id for record in sender.delivered] == [uuid5(row.id, "severity:critical")]
    assert "escalated" not in sender.delivered[0].body


async def test_an_escalation_observed_while_the_first_page_is_in_flight_still_pages() -> None:
    """The in-flight warning send acknowledges warning coverage; the next dispatch pages."""
    store = InMemoryAlertStore()
    sender = _BlockingSender()
    service = _service(store, sender)
    finding = _uncovered()
    first = asyncio.create_task(service.apply((finding,), now=_NOW))
    await sender.started.wait()
    await service.apply(
        (replace(finding, severity=_CRITICAL),), now=_NOW + timedelta(seconds=1), dispatch=False
    )
    sender.release.set()
    await first
    assert (await _row(store, finding.subject)).covered_severity is _WARNING
    await service.dispatch_pending(now=_NOW + timedelta(seconds=2))
    assert [record.severity.value for record in sender.delivered] == ["warning", "error"]


async def test_concurrent_dispatchers_page_an_escalation_once() -> None:
    """The claim compare-and-set admits one escalation send."""
    store = InMemoryAlertStore()
    recorder = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    finding = _uncovered()
    await _service(store, recorder).apply((finding,), now=_NOW)
    await _service(store, recorder).apply(
        (replace(finding, severity=_CRITICAL),), now=_NOW + timedelta(seconds=1), dispatch=False
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


async def test_flapping_severity_keeps_at_most_one_budget_per_level() -> None:
    """Severities rise three levels at most, so a failing webhook sees bounded attempts."""
    store = InMemoryAlertStore()
    failing = _FailureSender()
    service = _service(store, failing, attempts=2)
    finding = _uncovered()
    for step in range(30):
        severity = (AlertSeverity.INFO, _WARNING, _CRITICAL)[step % 3]
        await service.apply(
            (replace(finding, severity=severity),), now=_NOW + timedelta(seconds=61 * step)
        )
    assert failing.calls <= 6


async def test_an_entry_block_escalated_in_place_after_its_page_is_paged_at_once() -> None:
    """It no longer waits to clear and return: the open row is paged at critical."""
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = _service(store, sender)
    critical = _entry_block()
    warning = replace(critical, severity=_WARNING)
    for minute in range(7):
        await service.apply((warning,), now=_NOW + timedelta(minutes=minute))
    assert len(sender.delivered) == 1
    await service.apply((critical,), now=_NOW + timedelta(minutes=7))
    assert [record.title for record in sender.delivered] == [
        "[thytrader] WARNING: FLEET_ENTRIES_BLOCKED",
        "[thytrader] CRITICAL: FLEET_ENTRIES_BLOCKED",
    ]


def test_page_ids_are_one_per_alert_and_severity() -> None:
    """Every retry of one page keeps its id; pages at different severities never share one."""
    alert_id = uuid4()
    row = replace(new_alert(_uncovered(_CRITICAL), now=_NOW), id=alert_id)
    assert page_id(row) == uuid5(alert_id, "severity:critical")
    assert page_id(replace(row, covered_severity=_WARNING)) == page_id(row)
    assert page_id(replace(row, severity=_WARNING)) != page_id(row)
    assert page_id(replace(row, id=uuid4())) != page_id(row)


def test_the_report_judges_exhaustion_per_severity() -> None:
    """Warning exhausted, critical not yet: the report must not call the escalation exhausted."""
    row = replace(
        new_alert(_uncovered(_CRITICAL), now=_NOW),
        delivery_status="failed",
        delivery_attempts=3,
        warning_attempts=2,
        critical_attempts=1,
    )
    assert _item(row, max_attempts=2).delivery.status == "failed"
    assert _item(replace(row, critical_attempts=2), max_attempts=2).delivery.status == "exhausted"
