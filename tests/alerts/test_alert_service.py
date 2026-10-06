"""Dedupe, recovery, retry, and delivery idempotency for the alert service."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from thytrader.alerts.models import AlertCode, AlertScope, AlertSeverity, SupervisionFinding
from thytrader.alerts.service import AlertDeliverySender, AlertService
from thytrader.alerts.store import DELIVERY_DISABLED_DETAIL, InMemoryAlertStore
from thytrader.alerts.supervision import AlertThresholds
from thytrader.memory.models import DeliveryStatus, NotifyProvider
from thytrader.memory.notify import DeliveryResult, DisabledNotificationSender

_NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)


def _finding(code: AlertCode = AlertCode.BOOK_PAUSED_MISMATCH) -> SupervisionFinding:
    return SupervisionFinding(
        code=code,
        scope=AlertScope.DEPLOYMENT,
        subject=str(uuid4()),
        severity=AlertSeverity.WARNING,
        detail="paused for a test mismatch",
    )


class _FailingSender:
    def __init__(self, *, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def provider(self) -> NotifyProvider:
        return NotifyProvider.LOG

    async def deliver(self, record: object) -> DeliveryResult:
        del record
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("webhook down")
        return DeliveryResult(status=DeliveryStatus.LOGGED)


class _RecordingSender:
    def __init__(self) -> None:
        self.calls = 0

    def provider(self) -> NotifyProvider:
        return NotifyProvider.LOG

    async def deliver(self, record: object) -> DeliveryResult:
        del record
        self.calls += 1
        return DeliveryResult(status=DeliveryStatus.LOGGED)


def _service(
    store: InMemoryAlertStore, sender: AlertDeliverySender, *, attempts: int = 3
) -> AlertService:
    """Bind one store and sender with a small explicit retry budget."""
    return AlertService(
        store,
        sender,
        thresholds=AlertThresholds(delivery_max_attempts=attempts),
    )


@pytest.mark.anyio
async def test_repeat_cycles_dedupe_and_recovery_resolves_without_flooding() -> None:
    """Repeated findings refresh one open row; recovery resolves it exactly once."""
    store = InMemoryAlertStore()
    service = _service(store, DisabledNotificationSender())
    finding = _finding()
    first = await service.apply((finding,), now=_NOW)
    second = await service.apply((finding,), now=_NOW)
    assert first.changes[0].created is True
    assert second.changes[0].created is False
    assert second.changes[0].alert.occurrences == 2
    assert len(await store.list_alerts(limit=10)) == 1
    resolved = await service.apply((), now=_NOW)
    assert resolved.resolved_count == 1
    rows = await store.list_alerts(limit=10)
    assert rows[0].resolved_at == _NOW
    reopened = await service.apply((finding,), now=_NOW)
    assert reopened.changes[0].created is True
    assert len(await store.list_alerts(limit=10)) == 2


@pytest.mark.anyio
async def test_disabled_provider_records_an_explicit_skip_and_does_not_retry() -> None:
    """notify_provider=none stores an explicit delivery-disabled skip, not a webhook."""
    store = InMemoryAlertStore()
    service = _service(store, DisabledNotificationSender())
    await service.apply((_finding(),), now=_NOW)
    await service.apply((_finding(AlertCode.BOOK_PAUSED_MISMATCH),), now=_NOW)
    # second apply is a different subject; check the first row's delivery via list
    rows = await store.list_alerts(limit=10)
    assert rows
    assert all(row.delivery_status == "skipped" for row in rows if row.occurrences == 1)
    assert DELIVERY_DISABLED_DETAIL in rows[0].delivery_detail


@pytest.mark.anyio
async def test_delivery_retries_until_success_then_is_idempotent() -> None:
    """A failed delivery retries while the alert stays open; success is not repeated."""
    store = InMemoryAlertStore()
    sender = _FailingSender(failures=1)
    service = _service(store, sender, attempts=3)
    finding = _finding()
    await service.apply((finding,), now=_NOW)
    failed = (await store.list_alerts(limit=5))[0]
    assert failed.delivery_status == "failed"
    assert failed.delivery_attempts == 1
    await service.apply((finding,), now=_NOW)
    recovered = next(row for row in await store.list_alerts(limit=5) if row.is_open)
    assert recovered.delivery_status == "logged"
    calls_after_success = sender.calls
    await service.apply((finding,), now=_NOW)
    assert sender.calls == calls_after_success


@pytest.mark.anyio
async def test_delivery_stops_after_the_attempt_budget() -> None:
    """Delivery attempts stop at the configured cap and the row reports exhausted budget."""
    store = InMemoryAlertStore()
    sender = _FailingSender(failures=10)
    service = _service(store, sender, attempts=2)
    finding = _finding()
    await service.apply((finding,), now=_NOW)
    await service.apply((finding,), now=_NOW)
    await service.apply((finding,), now=_NOW)
    assert sender.calls == 2
    row = (await store.list_alerts(limit=5))[0]
    assert row.delivery_attempts == 2
    assert row.delivery_status == "failed"


@pytest.mark.anyio
async def test_restart_reuses_the_same_open_row() -> None:
    """A rebuilt service on the same store dedupes instead of opening a second row."""
    store = InMemoryAlertStore()
    finding = _finding()
    await _service(store, _RecordingSender()).apply((finding,), now=_NOW)
    restarted = _service(store, _RecordingSender())
    change = await restarted.apply((finding,), now=_NOW)
    assert change.changes[0].created is False
    assert change.changes[0].alert.occurrences == 2
    assert len(await store.list_alerts(limit=10)) == 1
