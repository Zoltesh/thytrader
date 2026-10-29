"""Each delivery claim is stamped at its own send, not at the batch start."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from thytrader.alerts import service as alert_service_module
from thytrader.alerts.models import AlertCode, AlertScope, AlertSeverity, SupervisionFinding
from thytrader.alerts.service import AlertService
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.alerts.supervision_inputs import AlertThresholds
from thytrader.config import NotifyProvider
from thytrader.memory.models import DeliveryStatus, NotificationRecord
from thytrader.memory.notify import DeliveryResult

_NOW = datetime(2026, 3, 2, 12, tzinfo=UTC)
pytestmark = pytest.mark.anyio


class _Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


class _SlowSender:
    """Each send takes 70s of wall time and records the claim lease it was sent under."""

    def __init__(self, store: InMemoryAlertStore, clock: _Clock) -> None:
        self.store = store
        self.clock = clock
        self.expiries: list[datetime] = []

    def provider(self) -> NotifyProvider:
        return NotifyProvider.WEBHOOK

    async def deliver(self, record: NotificationRecord) -> DeliveryResult:
        del record
        claimed = [
            row.delivery_expires_at
            for row in await self.store.list_open_alerts()
            if row.delivery_token is not None and row.delivery_expires_at is not None
        ]
        self.expiries.extend(claimed)
        self.clock.value += 70
        return DeliveryResult(status=DeliveryStatus.DELIVERED)


def _finding() -> SupervisionFinding:
    return SupervisionFinding(
        code=AlertCode.WORKER_BOOK_FAILURES,
        scope=AlertScope.DEPLOYMENT,
        subject=str(uuid4()),
        severity=AlertSeverity.CRITICAL,
        detail="cycle failed",
    )


async def test_a_late_item_in_a_slow_batch_is_claimed_at_its_own_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claim stamped at the batch start would already be expired when a late item is sent."""
    clock = _Clock()
    monkeypatch.setattr(alert_service_module, "monotonic", clock, raising=False)
    store = InMemoryAlertStore()
    await store.record(_finding(), now=_NOW)
    await store.record(_finding(), now=_NOW)
    sender = _SlowSender(store, clock)
    service = AlertService(store, sender, thresholds=AlertThresholds())
    await service.dispatch_pending(now=_NOW)
    assert len(sender.expiries) == 2
    # The second send starts 70s into the batch; its 60s claim must outlast that send.
    assert sender.expiries[1] >= _NOW + timedelta(seconds=70 + 60)
