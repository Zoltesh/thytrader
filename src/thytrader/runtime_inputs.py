"""Context-local observation and deterministic replay of clock/identity inputs."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

MAX_INPUT_EVENTS = 5000


@dataclass(frozen=True, slots=True)
class RuntimeInput:
    """One nondeterministic input in the exact order consumed by orchestration."""

    kind: Literal["clock", "uuid"]
    value: str


class InputTape:
    """Observe defaults without changing them, or consume a strict bounded offline tape."""

    def __init__(self, *, replay: Sequence[RuntimeInput] | None = None) -> None:
        """An absent replay observes actual inputs; an empty replay permits no reads."""
        self.events: list[RuntimeInput] = []
        self._replay = tuple(replay) if replay is not None else None
        self._index = 0
        self.failed = False
        if self._replay is not None and len(self._replay) > MAX_INPUT_EVENTS:
            raise ValueError("runtime input tape exceeds budget")

    @property
    def complete(self) -> bool:
        """Require every scripted input to be consumed, not only a passing prefix."""
        return not self.failed and self._replay is not None and self._index == len(self._replay)

    def resolve(self, kind: Literal["clock", "uuid"], default: Callable[[], str]) -> str:
        """Resolve one input while preserving its position and operation kind."""
        if self._replay is not None:
            if self._index >= len(self._replay) or self._replay[self._index].kind != kind:
                self.failed = True
                raise ValueError("runtime input cause/order mismatch")
            event = self._replay[self._index]
            self._index += 1
            return event.value
        value = default()
        if len(self.events) < MAX_INPUT_EVENTS:
            self.events.append(RuntimeInput(kind, value))
        else:
            self.failed = True
        return value


_TAPE: ContextVar[InputTape | None] = ContextVar("runtime_input_tape", default=None)


@contextmanager
def runtime_input_scope(tape: InputTape) -> Iterator[None]:
    """Bind one task-local tape; default live behavior is restored even on failure."""
    token = _TAPE.set(tape)
    try:
        yield
    finally:
        _TAPE.reset(token)


def resolve_time() -> datetime:
    """Use the actual UTC clock unless an isolated replay supplies that exact input."""
    tape = _TAPE.get()
    if tape is None:
        return datetime.now(UTC)
    text = tape.resolve("clock", lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        tape.failed = True
        raise
    if value.utcoffset() != timedelta(0):
        tape.failed = True
        raise ValueError("clock tape requires UTC")
    return value.astimezone(UTC)


def resolve_id(default: Callable[[], UUID], *, timestamp_milliseconds: int | None = None) -> UUID:
    """Observe actual random UUIDs or replay them without touching global randomness."""
    tape = _TAPE.get()
    if tape is None:
        return default()
    try:
        value = UUID(tape.resolve("uuid", lambda: str(default())))
    except ValueError:
        tape.failed = True
        raise
    if value.version != 7 or (
        timestamp_milliseconds is not None and value.int >> 80 != timestamp_milliseconds
    ):
        tape.failed = True
        raise ValueError("UUID tape must preserve UUIDv7 timestamp contract")
    return value
