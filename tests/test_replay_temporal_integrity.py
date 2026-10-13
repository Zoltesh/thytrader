"""Operation spans and actual runtime clock reads must retain causal evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from thytrader.ids import uuid7
from thytrader.replay.models import canonical_bytes
from thytrader.replay.packages import read_input
from thytrader.runtime_inputs import InputTape, RuntimeInput, runtime_input_scope


@pytest.mark.parametrize("fault", ["reversed", "before", "after", "start_before", "return_after"])
def test_broker_observations_fit_operation_envelope(fault: str) -> None:
    """The independent impossible-span reproducer and both containment endpoints fail closed."""
    package = read_input(Path("tests/replay/fixtures/usdc-lifecycle.input.json"))
    step = package.steps[0]
    start = step.evaluation_started_at
    done = step.decision_completed_at
    if fault == "reversed":
        start, done = datetime(2099, 1, 1, tzinfo=UTC), datetime(1900, 1, 1, tzinfo=UTC)
    elif fault == "before":
        start = done = start - timedelta(microseconds=1)
    elif fault == "after":
        start = done = done + timedelta(microseconds=1)
    elif fault == "start_before":
        start -= timedelta(microseconds=1)
    else:
        done += timedelta(microseconds=1)
    event = step.broker_events[0].model_copy(update={"started_at": start, "completed_at": done})
    forged = step.model_copy(update={"broker_events": (event, *step.broker_events[1:])})
    with pytest.raises(ValueError):
        canonical_bytes(package.model_copy(update={"steps": (forged,)}))


@pytest.mark.parametrize("fault", ["future", "past", "decreasing"])
def test_actual_clock_reads_fit_operation_envelope(fault: str) -> None:
    """CLOCK values are observations, not historical identity timestamps."""
    package = read_input(Path("tests/replay/fixtures/no-signal.input.json"))
    step = package.steps[0]
    clocks = tuple(event for event in step.runtime_inputs if event.kind == "clock")
    assert len(clocks) >= 2
    if fault == "decreasing":
        events = tuple(reversed(clocks))
    else:
        at = datetime(2099 if fault == "future" else 1900, 1, 1, tzinfo=UTC)
        events = (RuntimeInput("clock", at.isoformat()),)
    forged = step.model_copy(update={"runtime_inputs": events})
    with pytest.raises(ValueError):
        canonical_bytes(package.model_copy(update={"steps": (forged,)}))


def test_equal_synthetic_instants_and_historical_domain_facts_are_valid() -> None:
    """Equal boundary reads and old book/order/fill facts remain legitimate replay inputs."""
    package = read_input(Path("tests/replay/fixtures/usdc-lifecycle.input.json"))
    assert package.steps[-1].before.deployment.created_at < package.steps[-1].evaluation_started_at
    assert package.steps[-1].before.orders[0].created_at < package.steps[-1].evaluation_started_at
    historical = package.steps[-1].before.orders[0].created_at
    identity = package.steps[-1].before.orders[0].id
    events = (RuntimeInput("uuid", str(identity)),)
    step = package.steps[-1].model_copy(update={"runtime_inputs": events})
    assert canonical_bytes(package.model_copy(update={"steps": (step,)}))
    tape = InputTape(replay=events)
    with runtime_input_scope(tape):
        assert uuid7(historical) == identity
    assert tape.complete
    assert canonical_bytes(package)
