"""In-memory fleet latch and idempotent operation log.

The same object is the entry gate bound to an in-memory execution store, so a
disarm and a concurrent start share one lock. PostgreSQL uses the row lock in
:mod:`thytrader.fleet_control.postgres` instead.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import hashlib
from typing import TYPE_CHECKING, Protocol

from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import ExecutionConflictError, ExecutionStoreError
from thytrader.fleet_control.admission import entry_inhibited_detail
from thytrader.fleet_control.commands import confirmed_command
from thytrader.fleet_control.models import (
    ExpectedInhibition,
    ExpectedTarget,
    FleetExecuteRequest,
    FleetOperation,
    InhibitionSnapshot,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager
    from datetime import datetime

    from thytrader.execution.store import ExecutionStore


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

    def validate_execution(self, execution: ExecutionStore) -> None:
        """Refuse mixed/non-durable command and receipt backends before effects."""
        ...

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

    def operation_guard(self, idempotency_key: str) -> AbstractAsyncContextManager[None]:
        """Serialize retries across all instances, including pending operations."""
        ...

    async def apply_latch(
        self, operation: FleetOperation, request: FleetExecuteRequest, *, now: datetime
    ) -> FleetOperation:
        """Commit the confirmed latch revision and its causal receipt atomically."""
        ...

    async def record_target(
        self,
        execution: ExecutionStore,
        operation: FleetOperation,
        expected: ExpectedTarget,
        *,
        now: datetime,
    ) -> FleetOperation:
        """Commit a revision-fenced lifecycle command and its receipt atomically."""
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

    def validate_execution(self, execution: ExecutionStore) -> None:
        """Permit only the explicit memory execution harness."""
        if not isinstance(execution, InMemoryExecutionStore):
            raise ExecutionStoreError("Memory fleet receipts require memory execution persistence.")

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

    @asynccontextmanager
    async def operation_guard(self, idempotency_key: str) -> AsyncIterator[None]:
        """Hold the lock serializing one idempotency key."""
        async with self._operation_locks.setdefault(idempotency_key, asyncio.Lock()):
            yield

    async def apply_latch(
        self, operation: FleetOperation, request: FleetExecuteRequest, *, now: datetime
    ) -> FleetOperation:
        """Apply the latch and save its receipt without an intervening await."""
        async with self._lock:
            if operation.latch_applied:
                return operation
            modes = modes_for(request.mode.value)
            require_inhibition_revisions(self._snapshot(), request.expected_inhibition, modes)
            for mode in modes:
                self._inhibited[mode] = request.action.value == "disarm"
                self._revisions[mode] += 1
            self._updated_at = now
            saved = replace(
                operation, inhibition=self._snapshot(), latch_applied=True, updated_at=now
            )
            self._operations[operation.idempotency_key] = saved
            return saved

    async def record_target(
        self,
        execution: ExecutionStore,
        operation: FleetOperation,
        expected: ExpectedTarget,
        *,
        now: datetime,
    ) -> FleetOperation:
        """Couple memory writes without a suspension after command persistence.

        Refuse mixed persistence backends: real PostgreSQL commands must never
        be coupled to a process-local, non-durable receipt.
        """
        self.validate_execution(execution)
        async with self._lock:
            snapshot = await execution.get_deployment(expected.deployment_id)
            updated, result = confirmed_command(
                snapshot.deployment, expected, operation.action, operation.mode, now
            )
            saved = replace(operation, targets=(*operation.targets, result), updated_at=now)
            if updated is not None:
                await execution.save_deployment(updated, expected_revision=expected.revision)
            self._operations[operation.idempotency_key] = saved
            return saved

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
    inhibition: ExpectedInhibition,
) -> str:
    """Canonical identity of one fleet request, excluding the idempotency key."""
    targets = ",".join(f"{deployment_id}:{revision}" for deployment_id, revision in expected)
    canonical = (
        f"action={action};mode={mode};live={int(live_acknowledged)};"
        f"empty={int(allow_empty_scope)};targets={targets};"
        f"paper_revision={inhibition.paper_revision};live_revision={inhibition.live_revision}"
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def require_inhibition_revisions(
    snapshot: InhibitionSnapshot, expected: ExpectedInhibition, modes: tuple[str, ...]
) -> None:
    """Reject absent or stale confirmations, including a repeated deliberate disarm."""
    actual = {"paper": snapshot.paper_revision, "live": snapshot.live_revision}
    confirmed = {"paper": expected.paper_revision, "live": expected.live_revision}
    for mode in modes:
        if confirmed[mode] is None or confirmed[mode] != actual[mode]:
            raise ExecutionConflictError(
                f"inhibition_revision_conflict: {mode} revision is {actual[mode]}, "
                f"not confirmed {confirmed[mode]}. Read a new fleet preview."
            )


def sorted_expected(expected: tuple[tuple[str, int], ...]) -> tuple[tuple[str, int], ...]:
    """Sort confirmed identities so fingerprint order does not depend on the client."""
    return tuple(sorted(expected, key=lambda item: item[0]))
