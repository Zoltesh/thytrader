"""Config-gated notification delivery. Tests inject fakes; default sends nothing."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from thytrader.config import NotifyProvider
from thytrader.memory.models import DeliveryStatus, NotificationRecord

if TYPE_CHECKING:
    from collections.abc import Callable

    from thytrader.config import Settings
    from thytrader.settings_yaml import SettingsStore

_logger = logging.getLogger(__name__)
_WEBHOOK_TIMEOUT_SECONDS = 10.0
WEBHOOK_TIMESTAMP_HEADER = "X-Webhook-Timestamp"
WEBHOOK_SIGNATURE_HEADER = "X-Webhook-Signature-V2"


def webhook_signature(secret: str, *, timestamp: str, body: bytes) -> str:
    """Hex HMAC-SHA256 of ``timestamp + "." + body`` (ADR 0138).

    Signing the exact request bytes with a timestamp lets a receiver reject forged or replayed
    alerts without sharing anything but the key.
    """
    message = timestamp.encode("ascii") + b"." + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


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
    """POST a redacted JSON body to an operator-configured URL, optionally HMAC-signed."""

    def __init__(
        self,
        webhook_url: str,
        *,
        signing_secret: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Bind one webhook URL and optional signing key; neither is ever logged or stored."""
        if not webhook_url.strip():
            raise ValueError("webhook URL must be non-empty")
        if signing_secret is not None and not signing_secret.strip():
            raise ValueError("webhook signing secret must be non-empty when set")
        self._webhook_url = webhook_url
        self._signing_secret = signing_secret
        self._clock = clock

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
        body = json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._signing_secret is not None:
            timestamp = str(int(self._clock()))
            headers[WEBHOOK_TIMESTAMP_HEADER] = timestamp
            headers[WEBHOOK_SIGNATURE_HEADER] = webhook_signature(
                self._signing_secret, timestamp=timestamp, body=body
            )
        request = Request(  # noqa: S310 - URL comes from operator Settings, not user input.
            self._webhook_url, data=body, headers=headers, method="POST"
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
        signing = settings.notify_webhook_secret
        return WebhookNotificationSender(
            secret.get_secret_value(),
            signing_secret=None if signing is None else signing.get_secret_value(),
        )
    return DisabledNotificationSender()


class ReloadingNotificationSender:
    """Rebuild the inner sender when YAML ``notify_provider`` changes."""

    def __init__(self, store: SettingsStore) -> None:
        """Bind one settings store. Webhook URLs stay inside the inner sender."""
        self._store = store
        self._inner: NotificationSender = notification_sender_from_settings(store.current())
        self._provider = self._inner.provider()

    def provider(self) -> NotifyProvider:
        """Return the current provider after a possible YAML reload."""
        return self._refresh().provider()

    async def deliver(self, record: NotificationRecord) -> DeliveryResult:
        """Deliver through the latest configured backend."""
        return await self._refresh().deliver(record)

    def _refresh(self) -> NotificationSender:
        """Swap the inner sender when the YAML notify provider changes."""
        settings = self._store.current()
        if settings.notify_provider is not self._provider:
            self._inner = notification_sender_from_settings(settings)
            self._provider = settings.notify_provider
        return self._inner
