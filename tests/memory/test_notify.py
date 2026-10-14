"""Notification sender tests with fakes; webhook never opens a live socket."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import hmac
import json
from typing import TYPE_CHECKING
from unittest.mock import patch
from urllib.error import URLError

from pydantic import SecretStr, ValidationError
import pytest

from thytrader.config import NotifyProvider, Settings
from thytrader.memory.models import ActorOrigin, DeliveryStatus, NotificationRecord, NotifySeverity
from thytrader.memory.notify import (
    RecordingNotificationSender,
    WebhookNotificationSender,
    notification_sender_from_settings,
    webhook_signature,
)

if TYPE_CHECKING:
    from urllib.request import Request

_HMAC_KEY = "k3y"
_BLANK = "  "


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


class _Response:
    """Minimal urlopen context manager returning one HTTP status."""

    status = 200

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _capture_post(sender: WebhookNotificationSender) -> Request:
    """Deliver once through a patched urlopen and return the exact request it received."""
    sent: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> _Response:
        del timeout
        sent.append(request)
        return _Response()

    with patch("thytrader.memory.notify.urlopen", side_effect=fake_urlopen):
        result = asyncio.run(sender.deliver(_record()))
    assert result.status is DeliveryStatus.DELIVERED
    assert len(sent) == 1
    return sent[0]


def test_webhook_sender_signs_the_exact_body_with_a_timestamp() -> None:
    """A configured secret adds an HMAC-SHA256 over ``timestamp.body`` the receiver can verify."""
    sender = WebhookNotificationSender(
        "https://example.test/hooks/x", signing_secret=_HMAC_KEY, clock=lambda: 1_790_000_000.9
    )
    request = _capture_post(sender)
    body = request.data
    assert isinstance(body, bytes)
    assert request.get_header("X-webhook-timestamp") == "1790000000"
    expected = hmac.new(b"k3y", b"1790000000." + body, hashlib.sha256).hexdigest()
    assert request.get_header("X-webhook-signature-v2") == expected
    assert webhook_signature("k3y", timestamp="1790000000", body=body) == expected
    assert json.loads(body)["kind"] == "notification"


def test_webhook_sender_without_secret_sends_no_signature_headers() -> None:
    """Unsigned delivery stays the default for receivers that do not verify."""
    request = _capture_post(WebhookNotificationSender("https://example.test/hooks/x"))
    assert request.get_header("X-webhook-timestamp") is None
    assert request.get_header("X-webhook-signature-v2") is None


def test_webhook_sender_rejects_a_blank_signing_secret() -> None:
    """A whitespace secret is a misconfiguration, never a silently unsigned sender."""
    with pytest.raises(ValueError, match="signing secret"):
        WebhookNotificationSender("https://example.test/hooks/x", signing_secret=_BLANK)


def test_settings_pass_the_signing_secret_to_the_webhook_sender() -> None:
    """THYTRADER_NOTIFY_WEBHOOK_SECRET reaches the sender, which then signs every POST."""
    settings = Settings(
        notify_provider=NotifyProvider.WEBHOOK,
        notify_webhook_url=SecretStr("https://example.test/hooks/x"),
        notify_webhook_secret=SecretStr("k3y"),
        _env_file=None,
    )
    sender = notification_sender_from_settings(settings)
    assert isinstance(sender, WebhookNotificationSender)
    request = _capture_post(sender)
    assert request.get_header("X-webhook-signature-v2") is not None


def test_settings_reject_a_signing_secret_without_a_webhook_url() -> None:
    """A secret with nowhere to send is rejected rather than silently ignored."""
    with pytest.raises(ValidationError, match="THYTRADER_NOTIFY_WEBHOOK_SECRET"):
        Settings(notify_webhook_secret=SecretStr("k3y"), _env_file=None)


def test_settings_treat_an_empty_signing_secret_as_absent() -> None:
    """Compose passes ``${THYTRADER_NOTIFY_WEBHOOK_SECRET:-}``; empty means unset."""
    assert Settings(notify_webhook_secret="", _env_file=None).notify_webhook_secret is None
