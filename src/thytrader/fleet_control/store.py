"""In-memory fleet latch and idempotent operation log.

The same object is the entry gate bound to an in-memory execution store, so a
disarm and a concurrent start share one lock. PostgreSQL uses the row lock in
:mod:`thytrader.fleet_control.postgres` instead.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Protocol

from thytrader.execution.models import ExecutionConflictError
from thytrader.fleet_control.admission import entry_inhibited_detail
from thytrader.fleet_control.models import FleetOperation, InhibitionSnapshot

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager
    from datetime import datetime


class EntryGate(Protocol):
    """Latch lock shared with an in-memory execution store."""

    def hold(self) -> AbstractAsyncContextManager[None]:
        """Hold the latch across a check and insert."""
        ...

    def raise_if_inhibited(self, mode: str, *, action: str) -> None:
        """Refuse a start or entry when the mode is inhibited."""
        ...

    async def read_inhibition(self) -> InhibitionSnapshot:
        """Return the current latch."""
        ...


class FleetControlStore(Protocol):
    """Durable latch and operation log used by the fleet service."""

    async def read_inhibition(self) -> InhibitionSnapshot:
        """Return the current per-mode latch."""
        ...

    async def inhibit(self, modes: tuple[str, ...], *, now: datetime) -> InhibitionSnapshot:
        """Set entry inhibition for each named mode. Idempotent when already set."""
        ...

    async def release(self, modes: tuple[str, ...], *, now: datetime) -> InhibitionSnapshot:
        """Clear entry inhibition for each named mode. Idempotent when already clear."""
        ...

    async def get_operation(self, idempotency_key: str) -> FleetOperation | None:
        """Return a stored operation, if this key was already accepted."""
        ...

    async def insert_operation(self, operation: FleetOperation) -> bool:
        """Insert a pending operation. False means the key already exists."""
        ...

    async def save_operation(self, operation: FleetOperation) -> None:
        """Replace the stored operation for its idempotency key."""
        ...

    def operation_guard(self, idempotency_key: str) -> asyncio.Lock:
        """Serialize retries of one idempotency key."""
        ...


class InMemoryFleetControlStore:
    """Process-local latch for tests and database-free API processes."""

    def __init__(self) -> None:
        """Start with both modes admitted and no operations."""
        self._lock = asyncio.Lock()
        self._inhibited = {"paper": False, "live": False}
        self._revisions = {"paper": 0, "live": 0}
        self._updated_at: datetime | None = None
        self._operations: dict[str, FleetOperation] = {}
        self._operation_locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        """Hold the latch lock across a check and the following insert."""
        async with self._lock:
            yield

    def raise_if_inhibited(self, mode: str, *, action: str) -> None:
        """Refuse one risk-increasing action. Caller must hold :meth:`hold`."""
        if self._inhibited[mode]:
            raise ExecutionConflictError(entry_inhibited_detail(mode, action=action))

    async def read_inhibition(self) -> InhibitionSnapshot:
        """Return a copy of the latch."""
        async with self._lock:
            return self._snapshot()

    async def inhibit(self, modes: tuple[str, ...], *, now: datetime) -> InhibitionSnapshot:
        """Inhibit each mode, bumping revision only when the bit changes."""
        async with self._lock:
            self._set(modes, inhibited=True, now=now)
            return self._snapshot()

    async def release(self, modes: tuple[str, ...], *, now: datetime) -> InhibitionSnapshot:
        """Clear each mode, bumping revision only when the bit changes."""
        async with self._lock:
            self._set(modes, inhibited=False, now=now)
            return self._snapshot()

    async def get_operation(self, idempotency_key: str) -> FleetOperation | None:
        """Return the stored operation for one key."""
        async with self._lock:
            return self._operations.get(idempotency_key)

    async def insert_operation(self, operation: FleetOperation) -> bool:
        """Insert only when the key is new."""
        async with self._lock:
            if operation.idempotency_key in self._operations:
                return False
            self._operations[operation.idempotency_key] = operation
            return True

    async def save_operation(self, operation: FleetOperation) -> None:
        """Replace one stored operation."""
        async with self._lock:
            self._operations[operation.idempotency_key] = operation

    def operation_guard(self, idempotency_key: str) -> asyncio.Lock:
        """Return the lock that serializes one idempotency key."""
        return self._operation_locks.setdefault(idempotency_key, asyncio.Lock())

    def _set(self, modes: tuple[str, ...], *, inhibited: bool, now: datetime) -> None:
        """Apply one latch bit under the caller's lock."""
        for mode in modes:
            if self._inhibited[mode] is inhibited:
                continue
            self._inhibited[mode] = inhibited
            self._revisions[mode] += 1
            self._updated_at = now

    def _snapshot(self) -> InhibitionSnapshot:
        """Copy latch fields. Caller holds the lock."""
        return InhibitionSnapshot(
            paper_inhibited=self._inhibited["paper"],
            live_inhibited=self._inhibited["live"],
            paper_revision=self._revisions["paper"],
            live_revision=self._revisions["live"],
            updated_at=self._updated_at,
        )


def modes_for(scope: str) -> tuple[str, ...]:
    """Expand a fleet scope into latch modes."""
    if scope == "all":
        return ("paper", "live")
    if scope in {"paper", "live"}:
        return (scope,)
    raise ExecutionConflictError(f"Unsupported fleet mode scope: {scope}.")


def fingerprint_request(
    *,
    action: str,
    mode: str,
    expected: tuple[tuple[str, int], ...],
    live_acknowledged: bool,
    allow_empty_scope: bool,
) -> str:
    """Canonical identity of one fleet request, excluding the idempotency key."""
    targets = ",".join(f"{deployment_id}:{revision}" for deployment_id, revision in expected)
    return (
        f"action={action};mode={mode};live={int(live_acknowledged)};"
        f"empty={int(allow_empty_scope)};targets={targets}"
    )


def sorted_expected(expected: tuple[tuple[str, int], ...]) -> tuple[tuple[str, int], ...]:
    """Sort confirmed identities so fingerprint order does not depend on the client."""
    return tuple(sorted(expected, key=lambda item: item[0]))
