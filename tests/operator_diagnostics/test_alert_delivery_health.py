"""Alert delivery is degraded while live books hold risk without external delivery (ADR 0138)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from pydantic import SecretStr
import pytest

from thytrader.config import NotifyProvider, Settings
from thytrader.operator.alert_delivery_health import (
    alert_delivery_component,
    live_risk_book_count,
)
from thytrader.operator.models import ReportStatus
from thytrader.trading.models import DeploymentMode, DeploymentStatus

if TYPE_CHECKING:
    from thytrader.trading.store import ExecutionStore


@dataclass(frozen=True)
class _Book:
    """The two fields the delivery grader reads from a deployment."""

    mode: DeploymentMode
    status: DeploymentStatus


class _Fleet:
    """Execution-store stand-in that lists fixed deployments or fails to read."""

    def __init__(self, books: tuple[_Book, ...] = (), *, fail: bool = False) -> None:
        self._books = books
        self._fail = fail

    async def list_deployments(self) -> tuple[_Book, ...]:
        if self._fail:
            raise RuntimeError("database unavailable")
        return self._books


def _count(fleet: _Fleet) -> int | None:
    return asyncio.run(live_risk_book_count(cast("ExecutionStore", fleet)))


def _settings(provider: NotifyProvider) -> Settings:
    if provider is NotifyProvider.WEBHOOK:
        return Settings(
            notify_provider=provider,
            notify_webhook_url=SecretStr("https://example.test/hooks/x"),
            _env_file=None,
        )
    return Settings(notify_provider=provider, _env_file=None)


def test_live_risk_books_count_running_and_paused_live_only() -> None:
    """Paper books and stopped live books do not need an external pager."""
    fleet = _Fleet(
        (
            _Book(DeploymentMode.LIVE, DeploymentStatus.RUNNING),
            _Book(DeploymentMode.LIVE, DeploymentStatus.PAUSED),
            _Book(DeploymentMode.LIVE, DeploymentStatus.STOPPED),
            _Book(DeploymentMode.PAPER, DeploymentStatus.RUNNING),
        )
    )
    assert _count(fleet) == 2


def test_unreadable_fleet_is_unknown_not_zero() -> None:
    """A failed read must never be mistaken for an installation with no live books."""
    assert _count(_Fleet(fail=True)) is None
    assert asyncio.run(live_risk_book_count(None)) is None


@pytest.mark.parametrize("provider", [NotifyProvider.NONE, NotifyProvider.LOG])
def test_no_external_delivery_while_live_is_degraded(provider: NotifyProvider) -> None:
    """None and log-only both leave a live fleet without a pager."""
    report = alert_delivery_component(_settings(provider), live_books=3, failed_delivery=False)
    assert report.status is ReportStatus.DEGRADED
    assert report.reason_code == "DELIVERY_DISABLED_WHILE_LIVE"
    assert "3 live book(s)" in report.detail


def test_no_external_delivery_with_unknown_fleet_is_degraded() -> None:
    """Unknown live exposure fails closed to degraded."""
    report = alert_delivery_component(
        _settings(NotifyProvider.NONE), live_books=None, failed_delivery=False
    )
    assert report.status is ReportStatus.DEGRADED
    assert report.reason_code == "DELIVERY_DISABLED_LIVE_UNKNOWN"


def test_disabled_delivery_without_live_books_stays_healthy() -> None:
    """Paper-only installs keep the deliberate default-off provider without a warning."""
    report = alert_delivery_component(
        _settings(NotifyProvider.NONE), live_books=0, failed_delivery=False
    )
    assert report.status is ReportStatus.HEALTHY
    assert report.reason_code == "DELIVERY_DISABLED"


def test_webhook_delivery_is_healthy_until_an_open_alert_fails() -> None:
    """A configured webhook is healthy with live books; a failed open alert degrades it."""
    settings = _settings(NotifyProvider.WEBHOOK)
    healthy = alert_delivery_component(settings, live_books=5, failed_delivery=False)
    assert healthy.status is ReportStatus.HEALTHY
    assert healthy.reason_code == "DELIVERY_CONFIGURED"
    assert "example.test" not in healthy.detail
    failed = alert_delivery_component(settings, live_books=5, failed_delivery=True)
    assert failed.status is ReportStatus.DEGRADED
    assert failed.reason_code == "DELIVERY_FAILED"
