"""Clock and identity replay remains context-local, leaving normal production defaults intact."""

from __future__ import annotations

from datetime import UTC, datetime
import importlib
from uuid import UUID

import pytest

from thytrader.ids import uuid7
from thytrader.runtime_inputs import InputTape, RuntimeInput, runtime_input_scope
from thytrader.trading.ids import utc_now


def test_input_tape_replays_exact_clock_identity_order() -> None:
    """Existing orchestration calls consume recorded nondeterminism without global patches."""
    module = importlib.import_module("thytrader.ids")
    assert hasattr(module, "runtime_input_scope"), "context-scoped input replay is missing"

    tape = InputTape()
    with runtime_input_scope(tape):
        observed_time = utc_now()
        observed_id = uuid7(observed_time)
        second_time = utc_now()
    replay = InputTape(replay=tape.events)
    with runtime_input_scope(replay):
        assert utc_now() == observed_time
        assert uuid7(observed_time) == observed_id
        assert utc_now() == second_time
    assert replay.complete
    assert observed_time.tzinfo is UTC
    assert isinstance(observed_id, UUID)
    assert utc_now() >= second_time
    assert uuid7(datetime(2026, 1, 1, tzinfo=UTC)).version == 7


def test_swallowed_extra_clock_read_poisoned_tape() -> None:
    """An execution handler swallowing exhaustion cannot certify a successful consumed prefix."""
    tape = InputTape(replay=())
    with runtime_input_scope(tape), pytest.raises(ValueError):
        utc_now()
    assert not tape.complete


@pytest.mark.parametrize(
    "value",
    [
        "00000000-0000-0000-0000-000000000001",
        "019b76da-a800-7000-8000-000000000001",
    ],
)
def test_replayed_uuid_must_keep_version_and_requested_timestamp(value: str) -> None:
    """The UUID contract is not weakened by replaying a parseable arbitrary identifier."""
    tape = InputTape(replay=(RuntimeInput("uuid", value),))
    with runtime_input_scope(tape), pytest.raises(ValueError):
        uuid7(datetime(2026, 3, 1, tzinfo=UTC))
    assert not tape.complete


def test_nested_input_context_reset_after_failure() -> None:
    """Failure restores the outer task-local tape, then restores normal production defaults."""
    outer = InputTape(replay=(RuntimeInput("clock", "2026-01-01T00:00:00Z"),))
    with runtime_input_scope(outer):
        with runtime_input_scope(InputTape(replay=())), pytest.raises(ValueError):
            utc_now()
        assert utc_now() == datetime(2026, 1, 1, tzinfo=UTC)
    assert outer.complete
    assert utc_now().year != 1970


@pytest.mark.parametrize(
    "text", ["not-a-clock", "2026-01-01T00:00:00", "2026-01-01T00:00:00+01:00"]
)
def test_malformed_consumed_clock_cannot_certify_match(text: str) -> None:
    """A malformed consumed event stays failed even if a caller swallows the refusal."""
    tape = InputTape(replay=(RuntimeInput("clock", text),))
    with runtime_input_scope(tape), pytest.raises(ValueError):
        utc_now()
    assert not tape.complete
