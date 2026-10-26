"""Evidence-scoped recovery, restart watermarks and pre-send delivery claims."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from thytrader.alerts import service as alert_service_module
from thytrader.alerts.models import (
    AlertCheck,
    AlertCode,
    AlertScope,
    AlertSeverity,
    SupervisionFinding,
)
from thytrader.alerts.service import AlertService
from thytrader.alerts.store import DELIVERY_DISABLED_DETAIL, InMemoryAlertStore
from thytrader.alerts.supervision_inputs import AlertThresholds
from thytrader.config import NotifyProvider
from thytrader.memory.models import DeliveryStatus
from thytrader.memory.notify import (
    DeliveryResult,
    DisabledNotificationSender,
    RecordingNotificationSender,
)

if TYPE_CHECKING:
    from thytrader.alerts.service import AlertDeliverySender
    from thytrader.alerts.store import AlertStore
    from thytrader.memory.models import NotificationRecord

_NOW = datetime(2026, 3, 2, 12, tzinfo=UTC)
pytestmark = pytest.mark.anyio


def _finding() -> SupervisionFinding:
    """One exact durable worker-failure identity."""
    return SupervisionFinding(
        code=AlertCode.WORKER_BOOK_FAILURES,
        scope=AlertScope.DEPLOYMENT,
        subject=str(uuid4()),
        severity=AlertSeverity.WARNING,
        detail="cycle failed",
    )


def _service(store: AlertStore, sender: AlertDeliverySender | None = None) -> AlertService:
    """Rebuild a service without resetting persisted observation/delivery state."""
    return AlertService(store, sender or DisabledNotificationSender(), thresholds=AlertThresholds())


async def assert_disabled_preserves_active_claim(
    store: AlertStore, observer: AlertStore, *, finish_status: str
) -> None:
    """A disabled configuration view cannot revoke an in-flight sender's CAS token."""
    change = await store.record(_finding(), now=_NOW)
    first = await store.claim_delivery(
        change.alert.id, provider="webhook", now=_NOW, max_attempts=2, ttl=timedelta(seconds=60)
    )
    assert first is not None
    assert (
        await observer.claim_delivery(
            change.alert.id,
            provider="none",
            now=_NOW + timedelta(seconds=1),
            max_attempts=2,
            ttl=timedelta(seconds=60),
        )
        is None
    )
    retained = next(row for row in await observer.list_open_alerts() if row.id == change.alert.id)
    assert retained.delivery_token == first.token
    assert retained.delivery_attempts == 1
    assert (
        await observer.claim_delivery(
            change.alert.id,
            provider="webhook",
            now=_NOW + timedelta(seconds=2),
            max_attempts=2,
            ttl=timedelta(seconds=60),
        )
        is None
    )
    await store.finish_delivery(
        change.alert.id, first.token, status=finish_status, detail="known outcome"
    )
    finished = next(row for row in await observer.list_open_alerts() if row.id == change.alert.id)
    assert finished.delivery_status == finish_status
    assert finished.delivery_token is None


@pytest.mark.parametrize("finish_status", ["delivered", "failed"])
async def test_disabled_observer_cannot_revoke_an_active_delivery(finish_status: str) -> None:
    """Memory claims obey the same active-owner rule as PostgreSQL."""
    store = InMemoryAlertStore()
    await assert_disabled_preserves_active_claim(store, store, finish_status=finish_status)


async def test_unknown_pass_after_restart_is_not_recovery() -> None:
    """No finding and no evaluated key says unknown, even after service restart."""
    store = InMemoryAlertStore()
    finding = _finding()
    await _service(store).apply((finding,), now=_NOW)
    application = await _service(store).apply((), now=_NOW + timedelta(seconds=1))
    assert application.resolved_count == 0
    assert len(await store.list_open_alerts()) == 1
    clear = await _service(store).apply(
        (), now=_NOW + timedelta(seconds=2), evaluated=(AlertCheck(finding.code, finding.subject),)
    )
    assert clear.resolved_count == 1
    later = await _service(store).apply((finding,), now=_NOW + timedelta(seconds=3))
    assert later.changes[0].created
    assert len(await store.list_alerts(limit=10)) == 2


async def test_partial_verified_recovery_leaves_other_checks_open() -> None:
    """Recovery for one check never clears another unknown book/check."""
    store = InMemoryAlertStore()
    a, b = _finding(), _finding()
    await _service(store).apply((a, b), now=_NOW)
    await _service(store).apply(
        (), now=_NOW + timedelta(seconds=1), evaluated=(AlertCheck(a.code, a.subject),)
    )
    assert {row.subject for row in await store.list_open_alerts()} == {b.subject}


async def test_out_of_order_and_replayed_checks_cannot_fabricate_recovery() -> None:
    """Watermarks reject an old clear, duplicate failure and late pre-recovery failure."""
    store = InMemoryAlertStore()
    finding = _finding()
    check = AlertCheck(finding.code, finding.subject)
    await _service(store).apply((finding,), now=_NOW + timedelta(seconds=2))
    await _service(store).apply((), now=_NOW + timedelta(seconds=1), evaluated=(check,))
    await _service(store).apply((finding,), now=_NOW + timedelta(seconds=2))
    assert (await store.list_open_alerts())[0].occurrences == 1
    await _service(store).apply((), now=_NOW + timedelta(seconds=3), evaluated=(check,))
    await _service(store).apply((finding,), now=_NOW + timedelta(seconds=2))
    assert not await store.list_open_alerts()


async def test_disabled_provider_is_durable_and_enabling_delivers_once() -> None:
    """None provider skips without spending retry slots; enabling can send the existing alert."""
    store = InMemoryAlertStore()
    finding = _finding()
    await _service(store).apply((finding,), now=_NOW)
    row = (await store.list_open_alerts())[0]
    assert row.delivery_status == "skipped"
    assert row.delivery_attempts == 0
    assert row.delivery_detail == DELIVERY_DISABLED_DETAIL
    sender = RecordingNotificationSender(provider=NotifyProvider.LOG)
    await _service(store, sender).apply((), now=_NOW + timedelta(seconds=1))
    await _service(store, sender).apply((), now=_NOW + timedelta(seconds=2))
    assert len(sender.delivered) == 1
    assert sender.delivered[0].id == row.id


class _FailureSender:
    """Untrusted callback that returns or raises secret-bearing failures."""

    def __init__(self, *, provider_error: bool = False, raises: bool = False) -> None:
        """Select which callback fails."""
        self.provider_error = provider_error
        self.raises = raises
        self.calls = 0

    def provider(self) -> NotifyProvider:
        """Never let this callback's exception text escape."""
        if self.provider_error:
            raise ValueError("https://secret.invalid/TOKEN")
        return NotifyProvider.WEBHOOK

    async def deliver(self, record: NotificationRecord) -> DeliveryResult:
        """Return deliberately unredacted text; the service must discard it."""
        del record
        self.calls += 1
        if self.raises:
            raise ValueError("https://secret.invalid/TOKEN")
        return DeliveryResult(status=DeliveryStatus.FAILED, detail="https://secret.invalid/TOKEN")


@pytest.mark.parametrize("provider_error,raises", [(True, False), (False, True), (False, False)])
async def test_callback_failures_do_not_leak_and_retries_are_bounded(
    provider_error: bool, raises: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """Provider/deliver callbacks and returned detail cannot persist or log secrets."""
    store = InMemoryAlertStore()
    finding = _finding()
    sender = _FailureSender(provider_error=provider_error, raises=raises)
    service = AlertService(store, sender, thresholds=AlertThresholds(delivery_max_attempts=2))
    for seconds in range(4):
        await service.apply((finding,), now=_NOW + timedelta(seconds=seconds))
    row = (await store.list_open_alerts())[0]
    assert sender.calls == (0 if provider_error else 2)
    assert "secret.invalid" not in row.delivery_detail + caplog.text
    assert "TOKEN" not in row.delivery_detail + caplog.text


class _BlockingSender(RecordingNotificationSender):
    """Hold a send long enough to race a second dispatcher or cancellation."""

    def __init__(self) -> None:
        """Start a deterministic send barrier."""
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def deliver(self, record: NotificationRecord) -> DeliveryResult:
        """Record the attempt then block before acknowledgement."""
        self.delivered.append(record)
        self.started.set()
        await self.release.wait()
        return self.result


async def test_concurrent_dispatchers_cannot_duplicate_an_inflight_attempt() -> None:
    """A CAS claim prevents a second service from sending during the active attempt."""
    store = InMemoryAlertStore()
    sender = _BlockingSender()
    first = asyncio.create_task(_service(store, sender).apply((_finding(),), now=_NOW))
    await sender.started.wait()
    await _service(store, sender).apply((), now=_NOW + timedelta(seconds=1))
    assert len(sender.delivered) == 1
    sender.release.set()
    await first
    assert (await store.list_open_alerts())[0].delivery_status == "logged"


async def test_send_ack_crash_retries_after_claim_expiry_with_stable_id() -> None:
    """An ambiguous crash consumes a retry slot; only an expired claim can retry."""
    store = InMemoryAlertStore()
    sender = _BlockingSender()
    task = asyncio.create_task(_service(store, sender).apply((_finding(),), now=_NOW))
    await sender.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    before = (await store.list_open_alerts())[0]
    assert before.delivery_attempts == 1
    retry = RecordingNotificationSender()
    await _service(store, retry).apply((), now=_NOW + timedelta(seconds=20))
    assert not retry.delivered
    await _service(store, retry).apply((), now=_NOW + timedelta(seconds=61))
    assert retry.delivered[0].id == sender.delivered[0].id
    assert (await store.list_open_alerts())[0].delivery_attempts == 2


def _critical() -> SupervisionFinding:
    """One critical durable identity, as a stop-cover incident would raise."""
    return SupervisionFinding(
        code=AlertCode.STOP_UNCOVERED,
        scope=AlertScope.DEPLOYMENT,
        subject=str(uuid4()),
        severity=AlertSeverity.CRITICAL,
        detail="open live book has no verified resting exit",
    )


async def test_min_severity_sends_critical_and_keeps_lower_alerts_local() -> None:
    """A critical minimum sends critical alerts only; warnings stay in the feed as skipped."""
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = AlertService(
        store, sender, thresholds=AlertThresholds(), min_severity=lambda: "critical"
    )
    await service.apply((_finding(), _critical()), now=_NOW)
    assert [record.title for record in sender.delivered] == ["[thytrader] CRITICAL: STOP_UNCOVERED"]
    warning = next(row for row in await store.list_open_alerts() if row.severity.value == "warning")
    assert warning.delivery_status == "skipped"
    assert warning.delivery_detail is not None
    assert "notify_min_severity=critical" in warning.delivery_detail


async def test_enabling_delivery_with_a_critical_minimum_does_not_flush_old_warnings() -> None:
    """Switching a provider on re-sends skipped open alerts; the minimum still filters them."""
    store = InMemoryAlertStore()
    await _service(store).apply((_finding(), _critical()), now=_NOW)
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = AlertService(
        store, sender, thresholds=AlertThresholds(), min_severity=lambda: "critical"
    )
    await service.dispatch_pending(now=_NOW + timedelta(seconds=1))
    await service.dispatch_pending(now=_NOW + timedelta(seconds=2))
    assert [record.title for record in sender.delivered] == ["[thytrader] CRITICAL: STOP_UNCOVERED"]


async def test_min_severity_is_read_live_and_skips_spend_no_attempts() -> None:
    """A YAML change applies at the next delivery; skipped alerts keep their retry budget.

    Dispatch revisits open alerts every 30 seconds. A skip must not consume an attempt, or a
    warning would exhaust its budget within minutes and never deliver after the minimum drops.
    """
    store = InMemoryAlertStore()
    minimum = ["critical"]
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = AlertService(
        store, sender, thresholds=AlertThresholds(), min_severity=lambda: minimum[0]
    )
    await service.apply((_finding(),), now=_NOW)
    for second in range(1, 10):
        await service.dispatch_pending(now=_NOW + timedelta(seconds=second))
    assert sender.delivered == []
    warning = (await store.list_open_alerts())[0]
    assert warning.delivery_status == "skipped"
    assert warning.delivery_attempts == 0
    minimum[0] = "info"
    await service.dispatch_pending(now=_NOW + timedelta(seconds=20))
    assert [record.id for record in sender.delivered] == [warning.id]


async def test_raising_the_minimum_never_erases_failed_delivery_evidence() -> None:
    """A warning whose delivery failed stays ``failed`` after the minimum moves above it."""
    store = InMemoryAlertStore()
    failing = AlertService(
        store, _FailureSender(raises=True), thresholds=AlertThresholds(delivery_max_attempts=1)
    )
    await failing.apply((_finding(),), now=_NOW)
    assert (await store.list_open_alerts())[0].delivery_status == "failed"
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    quiet = AlertService(
        store, sender, thresholds=AlertThresholds(), min_severity=lambda: "critical"
    )
    await quiet.dispatch_pending(now=_NOW + timedelta(minutes=5))
    row = (await store.list_open_alerts())[0]
    assert row.delivery_status == "failed"
    assert sender.delivered == []


def _entry_block() -> SupervisionFinding:
    """A fleet entry block from a one-poll venue read failure (exits continue)."""
    return SupervisionFinding(
        code=AlertCode.FLEET_ENTRIES_BLOCKED,
        scope=AlertScope.FLEET,
        subject="fleet:live:USDC",
        severity=AlertSeverity.CRITICAL,
        detail="futures_collateral=FUTURES_COLLATERAL_UNKNOWN",
    )


async def test_an_entry_block_pages_only_once_it_outlasts_the_grace() -> None:
    """A blip that clears inside the grace is kept locally; a lasting block still pages."""
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = AlertService(
        store,
        sender,
        thresholds=AlertThresholds(entry_block_grace_seconds=300),
        min_severity=lambda: "critical",
    )
    block = _entry_block()
    await service.apply((block, _critical()), now=_NOW)
    assert [record.title for record in sender.delivered] == ["[thytrader] CRITICAL: STOP_UNCOVERED"]
    await service.apply((block,), now=_NOW + timedelta(seconds=120))
    assert len(sender.delivered) == 1
    await service.apply((block,), now=_NOW + timedelta(seconds=301))
    assert sender.delivered[-1].title == "[thytrader] CRITICAL: FLEET_ENTRIES_BLOCKED"


async def test_an_entry_block_that_heals_inside_the_grace_is_never_paged() -> None:
    """Resolved before the grace ends: recorded, not delivered; a zero grace pages at once."""
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = AlertService(
        store, sender, thresholds=AlertThresholds(), min_severity=lambda: "critical"
    )
    block = _entry_block()
    await service.apply((block,), now=_NOW)
    await service.apply(
        (), now=_NOW + timedelta(seconds=90), evaluated=(AlertCheck(block.code, block.subject),)
    )
    await service.apply((), now=_NOW + timedelta(seconds=600))
    assert sender.delivered == []
    assert len(await store.list_alerts(limit=10)) == 1
    immediate = AlertService(
        InMemoryAlertStore(),
        sender,
        thresholds=AlertThresholds(entry_block_grace_seconds=0),
        min_severity=lambda: "critical",
    )
    await immediate.apply((block,), now=_NOW)
    assert [record.title for record in sender.delivered] == [
        "[thytrader] CRITICAL: FLEET_ENTRIES_BLOCKED"
    ]


async def test_a_lasting_entry_block_is_sent_by_dispatch_alone_after_the_grace() -> None:
    """After a restart no new finding arrives; the delivery loop still sends it once due."""
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = AlertService(
        store, sender, thresholds=AlertThresholds(), min_severity=lambda: "critical"
    )
    await service.apply((_entry_block(),), now=_NOW)
    restarted = AlertService(
        store, sender, thresholds=AlertThresholds(), min_severity=lambda: "critical"
    )
    await restarted.dispatch_pending(now=_NOW + timedelta(seconds=299))
    assert sender.delivered == []
    await restarted.dispatch_pending(now=_NOW + timedelta(seconds=300))
    assert [record.title for record in sender.delivered] == [
        "[thytrader] CRITICAL: FLEET_ENTRIES_BLOCKED"
    ]


async def test_a_flapping_entry_block_is_paged_once_for_its_episode() -> None:
    """Blocked 3 of every 4 minutes for an hour: one page, not none and not one per return."""
    await assert_flapping_block_pages_once(InMemoryAlertStore())


@pytest.mark.parametrize(("crowd", "minutes"), [(250, 60), (0, 900)])
async def test_a_flapping_entry_block_is_paged_once_however_crowded_or_long(
    crowd: int, minutes: int
) -> None:
    """Other open alerts, or a flap lasting many hours, never lose or repeat the episode page."""
    await assert_flapping_block_pages_once(InMemoryAlertStore(), crowd=crowd, minutes=minutes)


async def assert_flapping_block_pages_once(
    store: AlertStore, *, crowd: int = 0, minutes: int = 60
) -> None:
    """A block that keeps returning within the grace is one episode, paged once."""
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = AlertService(
        store, sender, thresholds=AlertThresholds(), min_severity=lambda: "critical"
    )
    others = tuple(
        replace(_finding(), subject=f"crowd-{index}", severity=AlertSeverity.WARNING)
        for index in range(crowd)
    )
    await service.apply(others, now=_NOW - timedelta(minutes=1))
    block = _entry_block()
    check = AlertCheck(block.code, block.subject)
    for minute in range(minutes):
        now = _NOW + timedelta(minutes=minute)
        if minute % 4 == 3:
            await service.apply((), now=now, evaluated=(check,))
        else:
            await service.apply((block,), now=now)
    assert [record.title for record in sender.delivered] == [
        "[thytrader] CRITICAL: FLEET_ENTRIES_BLOCKED"
    ]
    rows = await store.list_resolved(block.code.value, block.subject, limit=minutes)
    assert len(rows) == minutes // 4
    assert {row.delivery_status for row in rows} == {"delivered", "skipped", "pending"}


async def test_an_entry_block_escalated_past_its_page_is_paged_again() -> None:
    """A warning page does not cover the same block returning as critical."""
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = AlertService(store, sender, thresholds=AlertThresholds())
    warning = replace(_entry_block(), severity=AlertSeverity.WARNING)
    check = AlertCheck(warning.code, warning.subject)
    for minute in range(7):
        await service.apply((warning,), now=_NOW + timedelta(minutes=minute))
    await service.apply((), now=_NOW + timedelta(minutes=7), evaluated=(check,))
    await service.apply((_entry_block(),), now=_NOW + timedelta(minutes=8))
    assert [record.title for record in sender.delivered] == [
        "[thytrader] WARNING: FLEET_ENTRIES_BLOCKED",
        "[thytrader] CRITICAL: FLEET_ENTRIES_BLOCKED",
    ]


async def test_a_flap_that_fills_the_history_window_still_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A walk that uses up the window cannot let the episode start slide forward forever."""
    monkeypatch.setattr(alert_service_module, "_EPISODE_HISTORY", 20)
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = AlertService(
        store, sender, thresholds=AlertThresholds(entry_block_grace_seconds=3600)
    )
    block = _entry_block()
    check = AlertCheck(block.code, block.subject)
    for step in range(200):
        start = _NOW + timedelta(seconds=6 * step)
        await service.apply((block,), now=start, dispatch=False)
        await service.dispatch_pending(now=start + timedelta(seconds=2))
        await service.apply((), now=start + timedelta(seconds=5), evaluated=(check,))
    assert [record.title for record in sender.delivered] == [
        "[thytrader] CRITICAL: FLEET_ENTRIES_BLOCKED"
    ]


async def test_a_window_that_is_exactly_full_does_not_page_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a row beyond the window makes the episode's start unknown."""
    monkeypatch.setattr(alert_service_module, "_EPISODE_HISTORY", 20)
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = AlertService(
        store, sender, thresholds=AlertThresholds(entry_block_grace_seconds=3600)
    )
    block = _entry_block()
    check = AlertCheck(block.code, block.subject)
    for step in range(20):
        start = _NOW + timedelta(seconds=6 * step)
        await service.apply((block,), now=start, dispatch=False)
        await service.apply((), now=start + timedelta(seconds=5), evaluated=(check,))
    await service.apply((block,), now=_NOW + timedelta(seconds=120))
    assert sender.delivered == []
    await service.apply((), now=_NOW + timedelta(seconds=125), evaluated=(check,))
    await service.apply((block,), now=_NOW + timedelta(seconds=126))
    assert len(sender.delivered) == 1


async def test_a_row_past_the_window_after_a_gap_does_not_page_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The extra row counts only if it still joins the episode, not if a gap separates it."""
    monkeypatch.setattr(alert_service_module, "_EPISODE_HISTORY", 3)
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = AlertService(
        store, sender, thresholds=AlertThresholds(entry_block_grace_seconds=3600)
    )
    block = _entry_block()
    check = AlertCheck(block.code, block.subject)
    earlier = _NOW - timedelta(hours=5)
    await service.apply((block,), now=earlier, dispatch=False)
    await service.apply((), now=earlier + timedelta(seconds=5), evaluated=(check,))
    for step in range(3):
        start = _NOW + timedelta(seconds=15 * step)
        await service.apply((block,), now=start, dispatch=False)
        await service.apply((), now=start + timedelta(seconds=5), evaluated=(check,))
    await service.apply((block,), now=_NOW + timedelta(seconds=45))
    assert sender.delivered == []


async def test_an_in_place_severity_change_pages_by_what_was_sent() -> None:
    """Coverage is the severity sent: an escalation pages on return, a downgrade never does."""
    await assert_severity_changes_page_by_coverage(InMemoryAlertStore())


async def assert_severity_changes_page_by_coverage(store: AlertStore) -> None:
    """A warning page escalated in place, then a critical page downgraded in place."""
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = AlertService(store, sender, thresholds=AlertThresholds())
    critical = _entry_block()
    warning = replace(critical, severity=AlertSeverity.WARNING)
    check = AlertCheck(critical.code, critical.subject)
    for minute in range(7):
        await service.apply((warning,), now=_NOW + timedelta(minutes=minute))
    await service.apply((critical,), now=_NOW + timedelta(minutes=7))
    await service.apply((), now=_NOW + timedelta(minutes=8), evaluated=(check,))
    await service.apply((critical,), now=_NOW + timedelta(minutes=9))
    await service.apply((warning,), now=_NOW + timedelta(minutes=10))
    await service.apply((), now=_NOW + timedelta(minutes=11), evaluated=(check,))
    await service.apply((critical,), now=_NOW + timedelta(minutes=12))
    assert [record.title for record in sender.delivered] == [
        "[thytrader] WARNING: FLEET_ENTRIES_BLOCKED",
        "[thytrader] CRITICAL: FLEET_ENTRIES_BLOCKED",
    ]
    downgraded = (await store.list_resolved(critical.code.value, critical.subject, limit=1))[0]
    assert downgraded.severity is AlertSeverity.WARNING
    assert downgraded.covered_severity is AlertSeverity.CRITICAL
    returned = (await store.list_open_alerts())[0]
    assert returned.delivery_status == "skipped"
    assert returned.covered_severity is AlertSeverity.CRITICAL


async def test_a_row_sent_before_coverage_was_recorded_covers_nothing() -> None:
    """Unknown coverage never trusts the observed severity: a duplicate page, never a miss."""
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = AlertService(store, sender, thresholds=AlertThresholds())
    block = _entry_block()
    check = AlertCheck(block.code, block.subject)
    await service.apply((block,), now=_NOW, dispatch=False)
    identity = (block.code.value, block.subject)
    store.history[identity][-1] = replace(store.history[identity][-1], delivery_status="delivered")
    await service.apply((), now=_NOW + timedelta(minutes=6), evaluated=(check,))
    await service.apply((block,), now=_NOW + timedelta(minutes=7))
    assert [record.title for record in sender.delivered] == [
        "[thytrader] CRITICAL: FLEET_ENTRIES_BLOCKED"
    ]


async def test_a_continuation_that_escalates_before_delivery_still_pages() -> None:
    """A return skipped under a warning page that turns critical is paged, not covered."""
    store = InMemoryAlertStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.WEBHOOK)
    service = AlertService(store, sender, thresholds=AlertThresholds())
    critical = _entry_block()
    warning = replace(critical, severity=AlertSeverity.WARNING)
    check = AlertCheck(critical.code, critical.subject)
    for minute in range(6):
        await service.apply((warning,), now=_NOW + timedelta(minutes=minute))
    await service.apply((), now=_NOW + timedelta(minutes=6), evaluated=(check,))
    await service.apply((warning,), now=_NOW + timedelta(minutes=7))
    await service.apply((critical,), now=_NOW + timedelta(minutes=8), dispatch=False)
    await service.apply((), now=_NOW + timedelta(minutes=9), evaluated=(check,), dispatch=False)
    await service.apply((critical,), now=_NOW + timedelta(minutes=10))
    assert [record.title for record in sender.delivered] == [
        "[thytrader] WARNING: FLEET_ENTRIES_BLOCKED",
        "[thytrader] CRITICAL: FLEET_ENTRIES_BLOCKED",
    ]


async def test_a_send_acknowledged_after_recovery_still_covers_its_episode() -> None:
    """A return waits for an in-flight page; once acknowledged, that page covers it."""
    store = InMemoryAlertStore()
    sender = _BlockingSender()
    service = _service(store, sender)
    block = _entry_block()
    check = AlertCheck(block.code, block.subject)
    await service.apply((block,), now=_NOW, dispatch=False)
    sending = asyncio.create_task(service.dispatch_pending(now=_NOW + timedelta(seconds=300)))
    await sender.started.wait()
    await service.apply((), now=_NOW + timedelta(seconds=301), evaluated=(check,))
    await service.apply((block,), now=_NOW + timedelta(seconds=302))
    assert len(sender.delivered) == 1
    sender.release.set()
    await sending
    await service.dispatch_pending(now=_NOW + timedelta(seconds=303))
    assert len(sender.delivered) == 1
    paged = (await store.list_resolved(block.code.value, block.subject, limit=1))[0]
    assert paged.delivery_status == "logged"
    returned = (await store.list_open_alerts())[0]
    assert returned.delivery_status == "skipped"
    assert returned.delivery_detail.startswith("continues an entry-block episode")
    assert returned.covered_severity is AlertSeverity.CRITICAL


async def test_an_entry_block_with_delivery_off_records_skipped_at_once() -> None:
    """With no notifier the grace has nothing to hold: the row says delivery is disabled."""
    store = InMemoryAlertStore()
    service = AlertService(
        store,
        DisabledNotificationSender(),
        thresholds=AlertThresholds(),
        min_severity=lambda: "critical",
    )
    block = _entry_block()
    await service.apply((block,), now=_NOW)
    await service.apply(
        (), now=_NOW + timedelta(seconds=90), evaluated=(AlertCheck(block.code, block.subject),)
    )
    row = (await store.list_alerts(limit=1))[0]
    assert row.delivery_status == "skipped"
    assert row.delivery_detail == DELIVERY_DISABLED_DETAIL
