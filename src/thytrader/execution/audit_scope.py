"""Per-task audit binding for execution-path events that must never block orders.

Execution helpers (submit, reconcile) are shared by the API process and the
execution worker and do not take an audit store parameter. The worker binds one
store for the duration of a cycle; helpers call ``record_execution_audit`` which
is a no-op when nothing is bound and swallows audit storage failures so the
order path never fails because audit is unavailable.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import logging
from typing import TYPE_CHECKING

from thytrader.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventUnavailableError,
)
from thytrader.trading.ids import utc_now

if TYPE_CHECKING:
    from collections.abc import Iterator

    from thytrader.audit_events import AuditEventStore

_logger = logging.getLogger(__name__)
_SCOPE: ContextVar[AuditEventStore | None] = ContextVar("execution_audit_scope", default=None)
_MAX_DETAIL = 2048


@contextmanager
def execution_audit_scope(store: AuditEventStore | None) -> Iterator[None]:
    """Bind ``store`` for execution audit events inside this context. None is a no-op."""
    if store is None:
        yield
        return
    token = _SCOPE.set(store)
    try:
        yield
    finally:
        _SCOPE.reset(token)


async def record_execution_audit(
    *,
    action: str,
    outcome: AuditEventOutcome,
    detail: str,
    product_id: str | None = None,
) -> None:
    r"""Append one redacted runtime audit row when a store is bound.

    Args:
        action: Stable audit action name (``^[a-zA-Z0-9_\-:]+$``).
        outcome: Normalized outcome.
        detail: Operator-facing text; must not contain secrets. Truncated to 2048.
        product_id: Optional spot product the event concerns.
    """
    store = _SCOPE.get()
    if store is None:
        return
    event = AuditEvent(
        occurred_at=utc_now(),
        category=AuditEventCategory.RUNTIME,
        action=action,
        outcome=outcome,
        detail=detail[:_MAX_DETAIL],
        product_id=product_id[:32] if product_id else None,
    )
    try:
        await store.append(event)
    except AuditEventUnavailableError:
        _logger.warning("execution_audit_unavailable action=%s", action)
