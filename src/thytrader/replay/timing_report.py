"""Report observed decision and normalized broker-call boundaries, never inferred wire timings."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from thytrader.replay.models import FrozenModel
from thytrader.replay.steps import ReplayStep, TimingEvidence


class BrokerMethodTiming(FrozenModel):
    """Invocation/return of an injected broker method, not transport send/venue acknowledgement."""

    boundary: Literal["normalized_broker_method_invocation_return"] = (
        "normalized_broker_method_invocation_return"
    )
    operation: str
    client_order_id: str | None
    venue_order_id: str | None
    invoked_at: datetime
    returned_at: datetime
    error_category: str | None


class StepTimingReport(FrozenModel):
    """Observed decision timestamps and optional measured CPU/database/venue-call counters."""

    data_available_at: datetime
    evaluation_started_at: datetime
    decision_completed_at: datetime
    measured: TimingEvidence | None
    broker_methods: tuple[BrokerMethodTiming, ...]
    unavailable_reasons: tuple[str, ...]


def timing_report(step: ReplayStep) -> StepTimingReport:
    """Expose the retained method seams distinctly from absent queue and wire instrumentation."""
    return StepTimingReport(
        data_available_at=step.data_available_at,
        evaluation_started_at=step.evaluation_started_at,
        decision_completed_at=step.decision_completed_at,
        measured=step.timing,
        broker_methods=tuple(
            BrokerMethodTiming(
                operation=event.operation,
                client_order_id=event.request.client_order_id,
                venue_order_id=event.request.venue_order_id
                or (event.result.venue_order_id if event.result is not None else None),
                invoked_at=event.started_at,
                returned_at=event.completed_at,
                error_category=event.error,
            )
            for event in step.broker_events
        ),
        unavailable_reasons=step.timing.unavailable_reasons
        if step.timing is not None
        else (
            "DECISION_COUNTERS_UNAVAILABLE",
            "QUEUE_BOUNDARY_UNAVAILABLE",
            "WIRE_SEND_ACK_UNAVAILABLE",
        ),
    )
