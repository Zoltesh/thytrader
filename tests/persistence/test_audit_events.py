"""Unit tests for typed audit event domain models and storage contracts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
import pytest

from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventUnavailableError,
    DisabledAuditEventStore,
    InMemoryAuditEventStore,
)


def _valid_event(**overrides: object) -> AuditEvent:
    payload: dict[str, object] = {
        "occurred_at": datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC),
        "category": "connection",
        "action": "snapshot_recorded",
        "outcome": "success",
        "detail": "Portfolio snapshot recorded successfully",
    }
    payload.update(overrides)
    return AuditEvent.model_validate(payload)


def test_audit_event_valid_instantiation() -> None:
    """AuditEvent instantiates with valid parameters and defaults."""
    event = _valid_event()
    assert event.category == "connection"
    assert event.action == "snapshot_recorded"
    assert event.outcome == "success"
    assert event.detail == "Portfolio snapshot recorded successfully"
    assert event.provider is None
    assert event.product_id is None
    assert isinstance(event.id, UUID)
    assert event.recorded_at.tzinfo == UTC


def test_audit_event_rejects_naive_datetime() -> None:
    """Naive datetimes are rejected for both occurred_at and recorded_at."""

    class NaiveDateTime(datetime):
        @property
        def tzinfo(self) -> None:
            return None

    with pytest.raises(ValidationError):
        _valid_event(occurred_at=NaiveDateTime(2026, 8, 17, 12, 0, 0, tzinfo=UTC))

    with pytest.raises(ValidationError):
        _valid_event(recorded_at=NaiveDateTime(2026, 8, 17, 12, 0, 0, tzinfo=UTC))


def test_audit_event_rejects_unknown_fields_and_invalid_enums() -> None:
    """Extra fields and unrecognized category/outcome are strictly rejected."""
    with pytest.raises(ValidationError):
        _valid_event(unknown_field="injected")

    with pytest.raises(ValidationError):
        _valid_event(category="invalid_category")

    with pytest.raises(ValidationError):
        _valid_event(outcome="invalid_outcome")

    with pytest.raises(ValidationError):
        _valid_event(action="")


def test_disabled_store_fails_closed() -> None:
    """Disabled store allows append (noop) but raises on list_recent."""
    store = DisabledAuditEventStore()
    event = _valid_event()

    # Append is a silent no-op
    asyncio.run(store.append(event))

    # Reads fail closed with typed exception
    with pytest.raises(AuditEventUnavailableError, match="Audit event storage is unavailable"):
        asyncio.run(store.list_recent(limit=50))


def test_in_memory_store_appends_and_lists_recent_newest_first() -> None:
    """InMemoryAuditEventStore stores events and returns newest-first bounded list."""
    store = InMemoryAuditEventStore(max_capacity=5)
    e1 = _valid_event(
        occurred_at=datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
        action="action_1",
    )
    e2 = _valid_event(
        occurred_at=datetime(2026, 8, 17, 11, 0, 0, tzinfo=UTC),
        action="action_2",
    )
    e3 = _valid_event(
        occurred_at=datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC),
        action="action_3",
    )

    asyncio.run(store.append(e1))
    asyncio.run(store.append(e2))
    asyncio.run(store.append(e3))

    recent = asyncio.run(store.list_recent(limit=2))
    assert len(recent) == 2
    assert recent[0].action == "action_3"
    assert recent[1].action == "action_2"

    all_recent = asyncio.run(store.list_recent(limit=10))
    assert len(all_recent) == 3
    assert all_recent[0].action == "action_3"
    assert all_recent[1].action == "action_2"
    assert all_recent[2].action == "action_1"


def test_in_memory_store_caps_capacity() -> None:
    """InMemoryAuditEventStore drops oldest items when capacity is exceeded."""
    store = InMemoryAuditEventStore(max_capacity=3)
    for i in range(5):
        event = _valid_event(
            occurred_at=datetime(2026, 8, 17, 10 + i, 0, 0, tzinfo=UTC),
            action=f"action_{i}",
        )
        asyncio.run(store.append(event))

    recent = asyncio.run(store.list_recent(limit=10))
    assert len(recent) == 3
    assert recent[0].action == "action_4"
    assert recent[1].action == "action_3"
    assert recent[2].action == "action_2"
