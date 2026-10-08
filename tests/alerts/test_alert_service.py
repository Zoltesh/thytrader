"""Evidence-scoped recovery, restart watermarks and pre-send delivery claims."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

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


def _service(store: InMemoryAlertStore, sender: AlertDeliverySender | None = None) -> AlertService:
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
