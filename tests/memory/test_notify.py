"""Notification sender tests with fakes; webhook never opens a live socket."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import patch
from urllib.error import URLError

from pydantic import SecretStr

from thytrader.config import Settings
from thytrader.memory.models import (
    ActorOrigin,
    DeliveryStatus,
    NotificationRecord,
    NotifyProvider,
    NotifySeverity,
)
from thytrader.memory.notify import (
    RecordingNotificationSender,
    WebhookNotificationSender,
    notification_sender_from_settings,
)


def _record() -> NotificationRecord:
    """Build one notification row for sender tests."""
    instant = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    return NotificationRecord(
        occurred_at=instant,
        origin=ActorOrigin.HUMAN,
        title="Hello",
        body="Body",
        severity=NotifySeverity.INFO,
        provider=NotifyProvider.WEBHOOK,
        delivery_status=DeliveryStatus.SKIPPED,
    )


def test_recording_sender_never_opens_a_socket() -> None:
    """Tests inject RecordingNotificationSender instead of urllib."""
    sender = RecordingNotificationSender(provider=NotifyProvider.LOG)
    result = asyncio.run(sender.deliver(_record()))
    assert result.status is DeliveryStatus.LOGGED
    assert len(sender.delivered) == 1


def test_webhook_sender_redacts_failures_without_url() -> None:
    """Network errors become failed delivery without echoing the URL."""
    sender = WebhookNotificationSender("https://example.test/hooks/secret-token")
    with patch("thytrader.memory.notify.urlopen", side_effect=URLError("boom")):
        result = asyncio.run(sender.deliver(_record()))
    assert result.status is DeliveryStatus.FAILED
    assert result.detail == "webhook_unreachable"
    assert "secret-token" not in result.detail


def test_settings_build_disabled_sender_by_default() -> None:
    """Unconfigured notify stays off."""
    sender = notification_sender_from_settings(Settings(_env_file=None))
    assert sender.provider() is NotifyProvider.NONE


def test_settings_build_webhook_sender_when_configured() -> None:
    """Webhook provider binds the secret URL inside the sender only."""
    settings = Settings(
        notify_provider=NotifyProvider.WEBHOOK,
        notify_webhook_url=SecretStr("https://example.test/hooks/secret-token"),
        _env_file=None,
    )
    sender = notification_sender_from_settings(settings)
    assert sender.provider() is NotifyProvider.WEBHOOK
    assert not hasattr(sender, "webhook_url")
