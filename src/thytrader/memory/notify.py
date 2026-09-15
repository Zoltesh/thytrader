"""Config-gated notification delivery. Tests inject fakes; default sends nothing."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from thytrader.memory.models import (
    DeliveryStatus,
    NotificationRecord,
    NotifyProvider,
)

if TYPE_CHECKING:
    from thytrader.config import Settings

_logger = logging.getLogger(__name__)
_WEBHOOK_TIMEOUT_SECONDS = 10.0


class DeliveryResult:
    """Outcome of one provider attempt without echoing the webhook URL."""

    def __init__(self, *, status: DeliveryStatus, detail: str = "") -> None:
        """Record a delivery status and optional redacted detail."""
        self.status = status
        self.detail = detail


@runtime_checkable
class NotificationSender(Protocol):
    """Deliver one notification through a configured backend."""

    def provider(self) -> NotifyProvider:
        """Return the provider identity stored on the notification row."""
        ...

    async def deliver(self, record: NotificationRecord) -> DeliveryResult:
        """Attempt delivery. Must not log webhook URLs or secrets."""
        ...


class DisabledNotificationSender:
    """Default-off provider: persist the request as skipped, send nothing."""

    def provider(self) -> NotifyProvider:
        """Advertise the none provider."""
        return NotifyProvider.NONE

    async def deliver(self, record: NotificationRecord) -> DeliveryResult:
        """Skip external delivery."""
        del record
        return DeliveryResult(status=DeliveryStatus.SKIPPED, detail="notify_provider_none")


class LogNotificationSender:
    """Write a structured log line. No network."""

    def provider(self) -> NotifyProvider:
        """Advertise the log provider."""
        return NotifyProvider.LOG

    async def deliver(self, record: NotificationRecord) -> DeliveryResult:
        """Log origin, title, and severity without secrets."""
        _logger.info(
            "Notification %s origin=%s severity=%s title=%s",
            record.id,
            record.origin.value,
            record.severity.value,
            record.title,
        )
        return DeliveryResult(status=DeliveryStatus.LOGGED)


class WebhookNotificationSender:
    """POST a redacted JSON body to an operator-configured URL."""

    def __init__(self, webhook_url: str) -> None:
        """Bind one webhook URL that must never be logged or stored on the row."""
        if not webhook_url.strip():
            raise ValueError("webhook URL must be non-empty")
        self._webhook_url = webhook_url

    def provider(self) -> NotifyProvider:
        """Advertise the webhook provider."""
        return NotifyProvider.WEBHOOK

    async def deliver(self, record: NotificationRecord) -> DeliveryResult:
        """POST JSON off the event loop. Fail closed without including the URL."""
        return await asyncio.to_thread(self._post, record)

    def _post(self, record: NotificationRecord) -> DeliveryResult:
        """Blocking POST used via ``asyncio.to_thread``."""
        payload = {
            "schema_version": record.schema_version,
            "kind": "notification",
            "id": str(record.id),
            "origin": record.origin.value,
            "title": record.title,
            "body": record.body,
            "severity": record.severity.value,
            "occurred_at": record.occurred_at.isoformat(),
        }
        request = Request(  # noqa: S310 - URL comes from operator Settings, not user input.
            self._webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=_WEBHOOK_TIMEOUT_SECONDS) as response:  # noqa: S310
                status = int(response.status)
        except HTTPError as error:
            del error
            return DeliveryResult(status=DeliveryStatus.FAILED, detail="webhook_http_error")
        except URLError, OSError, TimeoutError, ValueError:
            return DeliveryResult(status=DeliveryStatus.FAILED, detail="webhook_unreachable")
        if status >= 400:
            return DeliveryResult(status=DeliveryStatus.FAILED, detail="webhook_http_error")
        return DeliveryResult(status=DeliveryStatus.DELIVERED)


class RecordingNotificationSender:
    """Test double that records deliveries and never opens a network socket."""

    def __init__(self, *, provider: NotifyProvider = NotifyProvider.LOG) -> None:
        """Start with an empty delivery log."""
        self._provider = provider
        self.delivered: list[NotificationRecord] = []
        self.result = DeliveryResult(
            status=DeliveryStatus.LOGGED
            if provider is NotifyProvider.LOG
            else DeliveryStatus.DELIVERED
        )

    def provider(self) -> NotifyProvider:
        """Return the configured fake provider."""
        return self._provider

    async def deliver(self, record: NotificationRecord) -> DeliveryResult:
        """Record the payload in process memory."""
        self.delivered.append(record)
        return self.result


def notification_sender_from_settings(settings: Settings) -> NotificationSender:
    """Build the configured sender. Webhook URLs stay inside the sender instance."""
    provider = settings.notify_provider
    if provider is NotifyProvider.LOG:
        return LogNotificationSender()
    if provider is NotifyProvider.WEBHOOK:
        secret = settings.notify_webhook_url
        if secret is None:
            raise ValueError("webhook notify requires THYTRADER_NOTIFY_WEBHOOK_URL")
        return WebhookNotificationSender(secret.get_secret_value())
    return DisabledNotificationSender()
