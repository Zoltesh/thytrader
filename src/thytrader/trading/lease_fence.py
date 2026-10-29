"""The worker lease an order intent must be written under (ADR 0153).

A leased worker pass binds its book and holder here around each intent write. Stores check
the book's current lease holder in the same transaction that inserts the intent, so a pass
that outlived its lease cannot record a submission for a book another worker has taken.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from thytrader.trading.models import ExecutionConflictError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from uuid import UUID

LEASE_TAKEN_DETAIL = "Another worker holds this deployment's lease."


class LeaseTakenError(ExecutionConflictError):
    """An intent was refused because another worker now leases its book.

    It is a conflict, never an idempotent duplicate: the caller must not answer it with an
    earlier order recorded under the same key.
    """


_FENCE: ContextVar[tuple[UUID, str] | None] = ContextVar("worker_lease_fence", default=None)


@contextmanager
def lease_fence(deployment_id: UUID, holder: str) -> Iterator[None]:
    """Require ``holder`` (or no holder) on ``deployment_id`` for intents written inside."""
    token = _FENCE.set((deployment_id, holder))
    try:
        yield
    finally:
        _FENCE.reset(token)


def fenced_holder(deployment_id: UUID) -> str | None:
    """The holder an intent for this book must be written under, or None when unfenced."""
    fence = _FENCE.get()
    if fence is None or fence[0] != deployment_id:
        return None
    return fence[1]


def require_lease_holder(deployment_id: UUID, current_holder: str | None) -> None:
    """Raise when a fenced intent's book is now leased by a different worker."""
    expected = fenced_holder(deployment_id)
    if expected is not None and current_holder is not None and current_holder != expected:
        raise LeaseTakenError(LEASE_TAKEN_DETAIL)
