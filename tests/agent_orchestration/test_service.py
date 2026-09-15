"""YOLO skip recording and status advertisement."""

from __future__ import annotations

import asyncio

from pydantic import ValidationError
import pytest

from thytrader.agent_orchestration.models import SkippedConfirmationRequest, YoloTier
from thytrader.agent_orchestration.service import (
    YoloSkipRejectedError,
    orchestration_status,
    record_skipped_confirmation,
)
from thytrader.config import Settings
from thytrader.persistence.audit_events import (
    AuditEventUnavailableError,
    DisabledAuditEventStore,
    InMemoryAuditEventStore,
)


def test_default_status_is_safe_with_live_hard_gate() -> None:
    """YOLO is off and live authority is never advertised."""
    status = orchestration_status(Settings(_env_file=None))
    assert status.confirmation_mode.value == "safe"
    assert status.yolo_enabled is False
    assert status.yolo_tiers == ()
    assert status.live_hard_gate is True
    assert status.live_authority is False
    assert status.allows(YoloTier.DATA) is False


def test_skip_rejected_when_yolo_is_off() -> None:
    """A skip audit must not succeed in Safe mode."""
    store = InMemoryAuditEventStore()
    request = SkippedConfirmationRequest(tier=YoloTier.DATA, command="watch-add")
    with pytest.raises(YoloSkipRejectedError, match="Pass --confirm"):
        asyncio.run(
            record_skipped_confirmation(
                settings=Settings(_env_file=None),
                store=store,
                request=request,
            )
        )
    assert asyncio.run(store.list_recent()) == ()


def test_skip_records_lane_audit_when_yolo_covers_tier() -> None:
    """Skipped confirmations write an info audit on the matching lane category."""
    store = InMemoryAuditEventStore()
    settings = Settings(yolo_enabled=True, yolo_tiers=(YoloTier.DATA,), _env_file=None)
    request = SkippedConfirmationRequest(tier=YoloTier.DATA, command="watch-add")
    recorded = asyncio.run(
        record_skipped_confirmation(settings=settings, store=store, request=request)
    )
    events = asyncio.run(store.list_recent())
    assert recorded.action == "confirm_skipped"
    assert recorded.category == "market_data"
    assert len(events) == 1
    assert events[0].action == "confirm_skipped"
    assert events[0].category.value == "market_data"
    assert events[0].outcome.value == "info"
    assert "tier=data" in events[0].detail


def test_skip_fails_closed_without_audit_store() -> None:
    """YOLO cannot skip confirmation when audit storage is disabled."""
    settings = Settings(yolo_enabled=True, yolo_tiers=(YoloTier.RESEARCH,), _env_file=None)
    request = SkippedConfirmationRequest(tier=YoloTier.RESEARCH, command="create-draft")
    with pytest.raises(AuditEventUnavailableError, match="cannot skip"):
        asyncio.run(
            record_skipped_confirmation(
                settings=settings,
                store=DisabledAuditEventStore(),
                request=request,
            )
        )


def test_skipped_confirmation_rejects_live_command_names_that_break_pattern() -> None:
    """Command names stay bounded machine tokens."""
    with pytest.raises(ValidationError):
        SkippedConfirmationRequest(tier=YoloTier.PAPER, command="start live")
