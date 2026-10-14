"""Alert delivery is degraded while live books hold risk without external delivery (ADR 0138)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

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
    from collections.abc import Sequence

    from thytrader.trading.store import ExecutionStore


@dataclass(frozen=True)
class _Book:
    """The deployment fields the delivery grader reads, plus a residual-exposure marker."""

    mode: DeploymentMode
    status: DeploymentStatus
    residual: bool = False
    futures_derisk_pending: bool = False
    id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class _Snapshot:
    deployment: _Book


class _Fleet:
    """Execution-store stand-in that lists fixed deployments or fails to read."""

    def __init__(
        self, books: tuple[_Book, ...] = (), *, fail: bool = False, fail_snapshots: bool = False
    ) -> None:
        self._books = books
        self._fail = fail
        self._fail_snapshots = fail_snapshots

    async def list_deployments(self) -> tuple[_Book, ...]:
        if self._fail:
            raise RuntimeError("database unavailable")
        return self._books

    async def get_deployments(self, ids: Sequence[UUID]) -> tuple[_Snapshot, ...]:
        if self._fail_snapshots:
            raise RuntimeError("database unavailable")
        by_id = {book.id: book for book in self._books}
        return tuple(_Snapshot(by_id[item]) for item in ids)


@pytest.fixture(autouse=True)
def _residual_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read residual exposure from the fake's marker; the real helper is tested elsewhere."""
    monkeypatch.setattr(
        "thytrader.operator.alert_delivery_health.snapshot_has_residual_exposure",
        lambda snapshot: snapshot.deployment.residual,
    )


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
    """Paper books and flat stopped live books do not need an external pager."""
    fleet = _Fleet(
        (
            _Book(DeploymentMode.LIVE, DeploymentStatus.RUNNING),
            _Book(DeploymentMode.LIVE, DeploymentStatus.PAUSED),
            _Book(DeploymentMode.LIVE, DeploymentStatus.STOPPED),
            _Book(DeploymentMode.PAPER, DeploymentStatus.RUNNING),
        )
    )
    assert _count(fleet) == 2


def test_stopped_live_books_with_residual_risk_still_count() -> None:
    """Managed shutdown keeps inventory and protection, so a stopped book can still need a pager."""
    fleet = _Fleet(
        (
            _Book(DeploymentMode.LIVE, DeploymentStatus.STOPPED, residual=True),
            _Book(DeploymentMode.LIVE, DeploymentStatus.STOPPED, futures_derisk_pending=True),
            _Book(DeploymentMode.LIVE, DeploymentStatus.STOPPED),
            _Book(DeploymentMode.PAPER, DeploymentStatus.STOPPED, residual=True),
        )
    )
    assert _count(fleet) == 2


def test_unreadable_fleet_is_unknown_not_zero() -> None:
    """A failed read must never be mistaken for an installation with no live books."""
    assert _count(_Fleet(fail=True)) is None
    stopped = (_Book(DeploymentMode.LIVE, DeploymentStatus.STOPPED),)
    assert _count(_Fleet(stopped, fail_snapshots=True)) is None
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
